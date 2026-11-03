"""Disposable S13 OS permission and real SQLite lock wait, without mocks."""
import hashlib,json,os,pathlib,sqlite3,sys,time


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-disposable', action='store_true', required=True)
    parser.parse_args()
    os.environ['DATA_PROFILE_ID'] = 'uci-bike-sharing'
    root=pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'tests'))
    from test_monitor_inspection import MonitorInspectionTests
    from bike_mlops.monitor_inspection import inspect_monitor_run
    from bike_mlops.config import DATA_PROFILE_ID,TARGET_CONTRACT_ID
    if os.geteuid()==0:
        raise SystemExit('Must use an unprivileged account for chmod denial')
    x=MonitorInspectionTests();x.setUp()
    def inspect():
        return inspect_monitor_run(x.csv,x.run_id,database_url=x.db_url,data_profile_id=DATA_PROFILE_ID,target_contract_id=TARGET_CONTRACT_ID)
    def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        with sqlite3.connect(x.db_path) as c:
            assert c.execute('PRAGMA journal_mode=DELETE').fetchone()[0]=='delete'
        before=digest(x.db_path); files=x._file_snapshot(x.paths); logical=x._logical_snapshot(x.db_path)
        mode=x.db_path.stat().st_mode & 0o777
        try:
            x.db_path.chmod(0)
            permission=inspect()
            assert permission['status']=='database_unavailable' and permission['exit_code']==2
        finally:
            x.db_path.chmod(mode)
        assert digest(x.db_path)==before and x._file_snapshot(x.paths)==files
        assert inspect()['status']=='matched'
        locker=sqlite3.connect(x.db_path,isolation_level=None)
        try:
            locker.execute('BEGIN EXCLUSIVE')
            began=time.monotonic_ns(); lock=inspect(); ended=time.monotonic_ns()
            assert lock['status']=='database_unavailable' and lock['exit_code']==2
            assert 4 <= (ended-began)/1e9 < 10
        finally:
            locker.rollback();locker.close()
        assert digest(x.db_path)==before and x._file_snapshot(x.paths)==files
        assert x._logical_snapshot(x.db_path)==logical
        assert inspect()['status']=='matched'
        print(json.dumps(dict(status='passed',synthetic_only=True,real_Capital_evidence=False,
            unprivileged_euid=os.geteuid(),permission_status=permission['status'],permission_exit=permission['exit_code'],
            lock_status=lock['status'],lock_exit=lock['exit_code'],lock_elapsed_monotonic_seconds=(ended-began)/1e9,
            busy_timeout_seconds=5,journal_mode='delete',database_bytes_unchanged=True,logical_rows_unchanged=True,
            CSV_JSON_marker_unchanged=True,permissions_restored=True,post_release_matched=True)))
    finally:
        x.tearDown()


if __name__ == "__main__":
    main()
