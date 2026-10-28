# 合成staging統合検証の実行スクリプト（記録）

2026-10-28に一時ディレクトリと使い捨てCompose projectで実行したスクリプトを、その時点のまま記録する。合成データだけを扱った。記載された一時パス、候補ID、project名、port、versionは今回限りの値で、環境は検証後に削除した。これらのコードは実行環境の所有確認を自動で行わないため、既存のMLflowやDBに向けて実行せず、再利用時は隔離・安全確認とパラメーター化が必要である。

実行結果と制約は[作業ログ](../../work_log.md)および[合成ベンチマークの全要求記録](20261028-synthetic-staging-benchmark.json)を参照。

## 人工データ・候補・評価記録の生成

```python
from __future__ import annotations
import contextlib, io, json, sys, zipfile
from pathlib import Path
import pandas as pd
from bike_mlops.config import PROJECT_ROOT, DEFAULT_DATASET, DATASET_MANIFEST
from bike_mlops.evaluation_protocol import build_capital_protocol, save_protocol
from bike_mlops.training import HYPERPARAMETERS, train_candidate
from scripts.prepare_capitalbikeshare import aggregate_archives
from scripts import evaluate_capitalbikeshare_holdout

raw = PROJECT_ROOT / 'data/raw/capitalbikeshare/monthly'
raw.mkdir(parents=True, exist_ok=True)
lines = ['ride_id,started_at']
ride_id = 0
for t in pd.date_range('2024-01-01', '2024-02-01', freq='h', inclusive='left'):
    n = 5 + t.hour % 12 + (0 if t.dayofweek >= 5 else 25)
    for _ in range(n):
        lines.append(f'ride-{ride_id},{t:%Y-%m-%d %H:%M:%S}')
        ride_id += 1
with zipfile.ZipFile(raw / '202401-capitalbikeshare-tripdata.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    archive.writestr('trips.csv', '\n'.join(lines) + '\n')
aggregate_archives(['202401'], pd.Timestamp('2024-01-01'), pd.Timestamp('2024-02-01'), DEFAULT_DATASET, DATASET_MANIFEST, raw_dir=raw)
protocol = build_capital_protocol(DEFAULT_DATASET, DATASET_MANIFEST, training_cutoff='2024-01-15 00:00:00', validation_start='2024-01-15 00:00:00', validation_end='2024-01-22 00:00:00', evaluation_start='2024-01-22 03:00:00', evaluation_end='2024-01-29 00:00:00', incumbent_version=None, incumbent_artifact_sha256=None, incumbent_quality_gate=None, candidate_recipe={'model_family':'scikit-learn HistGradientBoostingRegressor in a Pipeline','hyperparameters':HYPERPARAMETERS}, raw_dir=raw)
protocol_path = PROJECT_ROOT / 'data/evaluations/synthetic-protocol.json'
save_protocol(protocol, protocol_path)
report = train_candidate(data_path=DEFAULT_DATASET, incumbent_version=None, source_manifest_path=DATASET_MANIFEST, register=False, evaluation_protocol_path=protocol_path)
print(json.dumps({'validation_gate':report['validation_gate_status'], 'validation':report['validation']['model'], 'baseline':report['validation']['baseline']}, ensure_ascii=False))
if not report['quality_gate_passed']:
    raise RuntimeError('Synthetic validation gate did not pass')
selection = PROJECT_ROOT / report['candidate_artifacts']['candidate_report']
output = PROJECT_ROOT / 'data/reports/capital-bikeshare/synthetic-final.json'
sys.argv = ['evaluate_capitalbikeshare_holdout.py', '--csv', str(DEFAULT_DATASET), '--source-manifest', str(DATASET_MANIFEST), '--selection-report', str(selection), '--output', str(output)]
with contextlib.redirect_stdout(io.StringIO()):
    evaluate_capitalbikeshare_holdout.main()
final = json.loads(output.read_text())
print(json.dumps({'candidate_id':report['candidate_artifacts']['candidate_id'], 'selection_report':str(selection), 'final_report':str(output), 'final_gate':final['final_accuracy_gate']['status'], 'final_candidate':final['candidate']['model'], 'final_baseline':final['historical_baseline']['model']}, ensure_ascii=False))
if not final['final_accuracy_gate']['passed']:
    raise RuntimeError('Synthetic final gate did not pass')

```

## MLflow・DB・HTTP・配信拒否の検証

```python
from __future__ import annotations
import json, math, os, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx
import pandas as pd
import psycopg
from mlflow.tracking import MlflowClient
from bike_mlops import capital_staging, cli
from bike_mlops.candidate_artifacts import load_candidate_model
from bike_mlops.config import PROJECT_ROOT, DEFAULT_DATASET
from bike_mlops.data import load_hourly
from bike_mlops.features import FEATURE_COLUMNS, feature_frame_for_dataset
from bike_mlops.model import load_registered_model, predict_non_negative

candidate_id = '4ac50f5613e507c71244992bd4271d19b5a8ad458bd4759dbf77fc92ac8f6274'
selection_path = PROJECT_ROOT / 'data/candidates' / candidate_id / 'candidate-report.json'
final_path = PROJECT_ROOT / 'data/reports/capital-bikeshare/synthetic-final.json'
selection = json.loads(selection_path.read_text())
env_file = PROJECT_ROOT / 'data/runtime-capital-staging/staging.env'
for line in env_file.read_text().splitlines():
    key, value = line.split('=', 1)
    os.environ[key] = value
version = os.environ['CAPITAL_STAGING_MODEL_VERSION']
artifact_sha = os.environ['CAPITAL_STAGING_REGISTERED_ARTIFACT_SHA256']
client = MlflowClient(tracking_uri=os.environ['MLFLOW_TRACKING_URI'])
result = capital_staging.register_blocked_capital_candidate(selection_path, final_path)
versions = client.search_model_versions("name = 'bike-demand-capital'")
matching = [v for v in versions if v.tags.get('candidate_id') == candidate_id]
assert result['model_version'] == version and len(matching) == 1
registered = client.get_model_version('bike-demand-capital', version)
assert registered.tags['deployment_permission'] == 'blocked'
assert registered.tags['operational_gate_status'] == 'not_evaluated'
assert registered.tags['artifact_sha256'] == artifact_sha

model, loaded_version, loaded_hash, _, _ = capital_staging.load_staging_registered_model()
assert loaded_version == version and loaded_hash == artifact_sha
try:
    client.set_model_version_tag('bike-demand-capital', version, 'candidate_id', '0' * 64)
    try:
        capital_staging.load_staging_registered_model()
        raise AssertionError('Changed candidate tag was accepted')
    except ValueError as error:
        assert 'candidate_id' in str(error)
finally:
    client.set_model_version_tag('bike-demand-capital', version, 'candidate_id', candidate_id)
try:
    client.set_model_version_tag('bike-demand-capital', version, 'artifact_sha256', '0' * 64)
    os.environ['CAPITAL_STAGING_REGISTERED_ARTIFACT_SHA256'] = '0' * 64
    try:
        capital_staging.load_staging_registered_model()
        raise AssertionError('Changed artifact hash was accepted')
    except ValueError as error:
        assert 'artifact SHA-256' in str(error)
finally:
    client.set_model_version_tag('bike-demand-capital', version, 'artifact_sha256', artifact_sha)
    os.environ['CAPITAL_STAGING_REGISTERED_ARTIFACT_SHA256'] = artifact_sha
try:
    load_registered_model('bike-demand-capital', version, os.environ['MLFLOW_TRACKING_URI'])
    raise AssertionError('Normal model loader accepted blocked candidate')
except ValueError as error:
    assert 'not eligible for deployment' in str(error)

request_id = 'synthetic-system-check-001'
target = '2024-01-10 10:00:00'
payload = {'request_id':request_id, 'target_hour':target, 'as_of':target, 'feature_contract_version':'bike-demand-features-v1', 'data_profile_id':'capital-bikeshare', 'target_contract_id':'capital-trip-starts-hourly-v1'}
with httpx.Client(base_url='http://127.0.0.1:8110', timeout=15, trust_env=False) as http:
    ready = http.get('/ready'); ready.raise_for_status()
    active = http.get('/version'); active.raise_for_status()
    assert ready.json()['candidate_id'] == candidate_id and ready.json()['deployment_permission'] == 'blocked'
    assert active.json()['model_version'] == version and active.json()['artifact_sha256'] == artifact_sha
    first = http.post('/predict', json=payload); first.raise_for_status()
    retry = http.post('/predict', json=payload); retry.raise_for_status()
    assert first.json() == retry.json()
    body = first.json()
    assert body['candidate_id'] == candidate_id and body['deployment_permission'] == 'blocked'
    changed = dict(payload, target_hour='2024-01-10 11:00:00', as_of='2024-01-10 11:00:00')
    conflict = http.post('/predict', json=changed)
    assert conflict.status_code == 409
frame = load_hourly(DEFAULT_DATASET)
features = feature_frame_for_dataset(frame)
idx = frame.index[frame['timestamp'] == pd.Timestamp(target)]
assert len(idx) == 1
candidate, _ = load_candidate_model(selection_path.parent / 'model.skops', selection['candidate_artifact']['sha256'])
offline = predict_non_negative(candidate, features.loc[idx, list(FEATURE_COLUMNS)])
assert math.isclose(offline, body['prediction'], rel_tol=1e-6, abs_tol=1e-5)
password = os.environ['APP_DB_PASSWORD']
with psycopg.connect(host='127.0.0.1', port=55434, dbname='bikeops', user='bike_app', password=password) as conn:
    row = conn.execute('SELECT candidate_id,model_version,artifact_sha256 FROM predictions WHERE request_id=%s', (request_id,)).fetchone()
    count = conn.execute('SELECT count(*) FROM predictions WHERE request_id=%s', (request_id,)).fetchone()[0]
    obs = conn.execute('SELECT count(*) FROM observations').fetchone()[0]
assert row == (candidate_id, version, artifact_sha) and count == 1 and obs == 744
with psycopg.connect(host='127.0.0.1', port=55433, dbname='bikeops', user='bike_app', password=password) as conn:
    has_table = conn.execute("SELECT to_regclass('public.predictions')").fetchone()[0]
    if has_table:
        assert conn.execute('SELECT count(*) FROM predictions WHERE request_id=%s', (request_id,)).fetchone()[0] == 0

state_path = cli.RUNTIME_DIR / 'deployment-state.json'
assert not state_path.exists()
try:
    cli.command_runtime_init(SimpleNamespace(version=version, approver='synthetic-tester', reason='isolated test', diagnostic=True))
    raise AssertionError('runtime init accepted a blocked candidate')
except ValueError as error:
    assert 'not eligible for deployment' in str(error)
assert not state_path.exists()
cli.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
state_path.write_text(json.dumps({'active_slot':'blue', 'active_version':'1', 'previous_versions':[]}))
original_state = state_path.read_bytes()
class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps({'model_version':'1', 'data_profile_id':'capital-bikeshare', 'target_contract_id':'capital-trip-starts-hourly-v1'}).encode()
        self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    def log_message(self, *args): pass
server = ThreadingHTTPServer(('127.0.0.1', 0), ProxyHandler)
thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
try:
    with patch.object(cli, '_proxy_url', return_value=f'http://127.0.0.1:{server.server_port}'), patch.object(cli, '_compose', side_effect=AssertionError('must not run Compose during gate rejection')):
        try:
            cli._promote(version, 'synthetic-tester', 'isolated test', rollback=False, diagnostic=True)
            raise AssertionError('promote accepted a blocked candidate')
        except ValueError as error:
            assert 'not eligible for deployment' in str(error)
finally:
    server.shutdown(); server.server_close(); thread.join()
assert state_path.read_bytes() == original_state
print(json.dumps({'candidate_id':candidate_id,'model_version':version,'same_candidate_registry_versions':len(matching),'tags':'blocked and not_evaluated','tag_mismatch_rejected':True,'artifact_hash_mismatch_rejected':True,'normal_loader_rejected':True,'runtime_init_rejected':True,'promote_rejected_with_synthetic_proxy':True,'ready_http':ready.status_code,'predict_http':first.status_code,'retry_equal':True,'changed_input_http':conflict.status_code,'offline_prediction':offline,'api_prediction':body['prediction'],'absolute_difference':abs(offline-body['prediction']),'staging_observations':obs,'staging_matching_prediction_rows':count,'capital_main_prediction_table':str(has_table)},indent=2))

```

## tag不一致時のready拒否と復帰

```python
from __future__ import annotations
import json, subprocess, time
import httpx
from mlflow.tracking import MlflowClient

project = 'bikemlopsit4utzqoxs'
compose = ['docker','compose','-p',project,'-f','compose.yaml','-f','compose.capital.yaml','-f','compose.capital-staging.yaml','-f','/tmp/bike-mlops-it-4UTZQoxs/compose-test-override.yaml']
name = 'bike-demand-capital'
version = '2'
candidate_id = '4ac50f5613e507c71244992bd4271d19b5a8ad458bd4759dbf77fc92ac8f6274'
client = MlflowClient(tracking_uri='http://127.0.0.1:5001')

def restart():
    subprocess.run(compose + ['restart','api-staging'], check=True, capture_output=True, text=True, timeout=30)

def wait_for_status(expected, seconds):
    end = time.monotonic() + seconds
    last = None
    with httpx.Client(timeout=3, trust_env=False) as http:
        while time.monotonic() < end:
            try:
                response = http.get('http://127.0.0.1:8110/ready')
                last = (response.status_code, response.text)
                if response.status_code == expected:
                    return response
            except httpx.HTTPError as error:
                last = type(error).__name__
            time.sleep(2)
    raise TimeoutError(f'API did not return {expected}; last={last}')

try:
    client.set_model_version_tag(name, version, 'candidate_id', '0' * 64)
    restart()
    rejected = wait_for_status(503, 85)
    assert 'candidate_id' in rejected.json()['detail'], rejected.text
finally:
    client.set_model_version_tag(name, version, 'candidate_id', candidate_id)
    restart()
    restored = wait_for_status(200, 60)
    assert restored.json()['candidate_id'] == candidate_id
print(json.dumps({'mismatched_registry_tag_ready_http':rejected.status_code,'rejection_mentions_candidate_id':True,'restored_ready_http':restored.status_code,'restored_candidate_id':candidate_id}, indent=2))

```

## 候補切替後の同一要求再送

```python
from __future__ import annotations
import json, subprocess, time
from pathlib import Path
import httpx
from mlflow.tracking import MlflowClient

root = Path('/tmp/bike-mlops-it-4UTZQoxs/261021')
env_file = root / 'data/runtime-capital-staging/staging.env'
original = env_file.read_text()
client = MlflowClient(tracking_uri='http://127.0.0.1:5001')
tags = client.get_model_version('bike-demand-capital','1').tags
old_candidate = tags['candidate_id']
current_candidate = '4ac50f5613e507c71244992bd4271d19b5a8ad458bd4759dbf77fc92ac8f6274'
values = {'MODEL_VERSION':'1','CAPITAL_STAGING_MODEL_VERSION':'1','CAPITAL_STAGING_CANDIDATE_ID':old_candidate,'CAPITAL_STAGING_CANDIDATE_ARTIFACT_SHA256':tags['candidate_model_sha256'],'CAPITAL_STAGING_SELECTION_REPORT_SHA256':tags['candidate_report_sha256'],'CAPITAL_STAGING_FINAL_REPORT_SHA256':tags['final_report_sha256'],'CAPITAL_STAGING_EVALUATION_PROTOCOL_SHA256':tags['evaluation_protocol_sha256'],'CAPITAL_STAGING_REGISTERED_ARTIFACT_SHA256':tags['artifact_sha256']}
compose = ['docker','compose','-p','bikemlopsit4utzqoxs','-f','compose.yaml','-f','compose.capital.yaml','-f','compose.capital-staging.yaml','-f','/tmp/bike-mlops-it-4UTZQoxs/compose-test-override.yaml']
def recreate():
    subprocess.run(compose + ['up','-d','--no-build','--force-recreate','--no-deps','api-staging'], check=True, capture_output=True, text=True, timeout=30)
def wait_ready(version):
    end = time.monotonic() + 60
    with httpx.Client(timeout=3, trust_env=False) as http:
        while time.monotonic() < end:
            try:
                r = http.get('http://127.0.0.1:8110/ready')
                if r.status_code == 200 and r.json().get('model_version') == version:
                    return r.json()
            except httpx.HTTPError:
                pass
            time.sleep(1)
    raise TimeoutError('staging API did not become ready with version ' + version)
payload = {'request_id':'synthetic-system-check-001','target_hour':'2024-01-10 10:00:00','as_of':'2024-01-10 10:00:00','feature_contract_version':'bike-demand-features-v1','data_profile_id':'capital-bikeshare','target_contract_id':'capital-trip-starts-hourly-v1'}
try:
    env_file.write_text(''.join(f'{key}={value}\n' for key,value in values.items()))
    recreate()
    ready = wait_ready('1')
    assert ready['candidate_id'] == old_candidate
    with httpx.Client(base_url='http://127.0.0.1:8110', timeout=15, trust_env=False) as http:
        saved = http.post('/predict',json=payload); saved.raise_for_status()
        fresh = http.post('/predict',json=dict(payload, request_id='synthetic-version1-check-001')); fresh.raise_for_status()
    assert saved.json()['model_version'] == '2' and saved.json()['candidate_id'] == current_candidate
    assert fresh.json()['model_version'] == '1' and fresh.json()['candidate_id'] == old_candidate
    assert saved.json()['deployment_permission'] == fresh.json()['deployment_permission'] == 'blocked'
finally:
    env_file.write_text(original)
    recreate()
    restored = wait_ready('2')
    assert restored['candidate_id'] == current_candidate
print(json.dumps({'temporary_active_version':ready['model_version'],'saved_retry_model_version':saved.json()['model_version'],'saved_retry_candidate_id':saved.json()['candidate_id'],'fresh_model_version':fresh.json()['model_version'],'fresh_candidate_id':fresh.json()['candidate_id'],'restored_model_version':restored['model_version']},indent=2))

```

## 合成1,050要求の測定

```python
from __future__ import annotations
import json, os, platform, subprocess, sys
from pathlib import Path
import psycopg
from bike_mlops.candidate_artifacts import sha256_file
from bike_mlops.config import PROJECT_ROOT
from bike_mlops.operational_benchmark import run_capital_operational_benchmark, write_benchmark_report

candidate_id = '4ac50f5613e507c71244992bd4271d19b5a8ad458bd4759dbf77fc92ac8f6274'
compose = ['docker','compose','-p','bikemlopsit4utzqoxs','-f','compose.yaml','-f','compose.capital.yaml','-f','compose.capital-staging.yaml','-f','/tmp/bike-mlops-it-4UTZQoxs/compose-test-override.yaml']
def resources():
    ids = subprocess.check_output(compose + ['ps','--status','running','-q','postgres','mlflow','postgres-staging','api-staging'], text=True, timeout=15).split()
    inspected = subprocess.check_output(['docker','inspect','--format','{{.Name}}|{{.Config.Image}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.Memory}}',*ids], text=True, timeout=15).splitlines()
    stats = subprocess.check_output(['docker','stats','--no-stream','--format','{{.Container}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}',*ids], text=True, timeout=20).splitlines()
    return {'scope':'synthetic disposable project', 'host':{'os':platform.platform(),'cpu_count':os.cpu_count(),'python':sys.version.split()[0]}, 'containers':inspected,'stats':stats}
report = run_capital_operational_benchmark(api_url='http://127.0.0.1:8110', model_version='2', candidate_id=candidate_id, start='2024-01-08 00:00:00', end='2024-01-15 00:00:00', resource_fn=resources)
output = PROJECT_ROOT / 'data/reports/capital-bikeshare/synthetic-operational.json'
write_benchmark_report(output, report)
observed = report['operational_gate']['observed']
assert report['completed'] and observed['warmup_requests'] == 50 and observed['measured_requests'] == 1000
assert observed['warmup_errors'] == 0 and observed['measured_errors'] == 0
with psycopg.connect(host='127.0.0.1', port=55434, dbname='bikeops', user='bike_app', password=os.environ['APP_DB_PASSWORD']) as conn:
    rows = conn.execute('SELECT count(*) FROM predictions WHERE request_id LIKE %s', (f"capital-op:{report['benchmark_id']}:%",)).fetchone()[0]
assert rows == 1050
print(json.dumps({'scope':'synthetic fixture only; not a real candidate operational result', 'benchmark_id':report['benchmark_id'], 'status':report['operational_gate']['status'], 'warmup':observed['warmup_requests'], 'requests':observed['measured_requests'], 'warmup_errors':observed['warmup_errors'], 'errors':observed['measured_errors'], 'p95_ms':observed['p95_ms'], 'max_ms':observed['max_ms'], 'audit_rows':rows, 'deployment_permission':report['deployment_permission']['status'], 'report_sha256':sha256_file(output)}, indent=2))

```
