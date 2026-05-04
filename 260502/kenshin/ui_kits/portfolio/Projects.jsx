// Projects.jsx — Selected work / case studies
const Projects = ({ onNav }) => {
  const [active, setActive] = React.useState(0);

  const projects = [
    {
      tag: 'Requirements & Architecture',
      title: 'ERP migration for a mid-size manufacturer',
      desc: 'Led requirements discovery across 6 departments, mapped 140 business processes, and produced a vendor-neutral system specification that cut RFP evaluation time by 60%.',
      outcomes: ['140 processes documented', 'Vendor selected in 8 weeks', '60% faster evaluation'],
      year: '2024',
      industry: 'Manufacturing',
    },
    {
      tag: 'Organisational Change',
      title: 'Digital transformation programme',
      desc: 'Designed and led a 12-month change programme for a 400-person operations team migrating from legacy workflows to a cloud-based platform.',
      outcomes: ['94% adoption at 6 months', 'Zero critical rollbacks', '30% process efficiency gain'],
      year: '2023',
      industry: 'Logistics',
    },
    {
      tag: 'Strategy & Roadmap',
      title: 'IT strategy for a scaling fintech',
      desc: 'Worked with the CTO and board to produce a 3-year IT roadmap aligned to business growth targets, including build/buy analysis and risk prioritisation.',
      outcomes: ['3-year roadmap approved', '12 initiatives scoped', 'Board presentation delivered'],
      year: '2023',
      industry: 'Fintech',
    },
  ];

  const p = projects[active];

  const sectionStyle = {
    padding: '96px 24px',
    borderTop: '1px solid #1E1E2A',
  };
  const innerStyle = { maxWidth: 1200, margin: '0 auto' };
  const gridStyle = {
    display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 48,
    alignItems: 'start',
  };
  const tabStyle = (i) => ({
    padding: '16px 20px',
    borderRadius: 8,
    border: `1px solid ${active === i ? '#2D2B5A' : 'transparent'}`,
    background: active === i ? '#16161F' : 'transparent',
    cursor: 'pointer',
    textAlign: 'left',
    transition: 'all 150ms ease',
    marginBottom: 4,
    width: '100%',
  });
  const detailStyle = {
    background: '#16161F',
    border: '1px solid #2A2A38',
    borderRadius: 10,
    padding: 36,
  };

  return (
    <section id="projects" style={sectionStyle}>
      <div style={innerStyle}>
        <div style={{ marginBottom: 56 }}>
          <div style={{ fontFamily:"'DM Sans',sans-serif", fontSize:11, fontWeight:600, letterSpacing:'0.12em', textTransform:'uppercase', color:'#5A5A72', marginBottom:12 }}>Selected work</div>
          <h2 style={{ fontFamily:"'Syne',sans-serif", fontSize:'clamp(28px,4vw,44px)', fontWeight:700, letterSpacing:'-0.02em', color:'#F0EEF8', lineHeight:1.15 }}>Projects</h2>
        </div>
        <div style={gridStyle}>
          {/* Tab list */}
          <div>
            {projects.map((p, i) => (
              <button key={i} style={tabStyle(i)} onClick={() => setActive(i)}
                onMouseEnter={e => { if (active !== i) e.currentTarget.style.background = '#16161F'; }}
                onMouseLeave={e => { if (active !== i) e.currentTarget.style.background = 'transparent'; }}
              >
                <div style={{ fontSize:11, color: active===i ? '#6C63FF' : '#5A5A72', fontWeight:600, letterSpacing:'0.06em', textTransform:'uppercase', marginBottom:6 }}>{p.tag}</div>
                <div style={{ fontFamily:"'Syne',sans-serif", fontSize:16, fontWeight:600, color: active===i ? '#F0EEF8' : '#8B8A9E', lineHeight:1.3 }}>{p.title}</div>
                <div style={{ fontSize:12, color:'#5A5A72', marginTop:6 }}>{p.year} · {p.industry}</div>
              </button>
            ))}
          </div>
          {/* Detail panel */}
          <div style={detailStyle}>
            <div style={{ fontSize:11, fontWeight:600, letterSpacing:'0.1em', textTransform:'uppercase', color:'#6C63FF', marginBottom:16 }}>{p.tag}</div>
            <h3 style={{ fontFamily:"'Syne',sans-serif", fontSize:22, fontWeight:700, color:'#F0EEF8', lineHeight:1.25, marginBottom:20 }}>{p.title}</h3>
            <p style={{ fontSize:15, color:'#8B8A9E', lineHeight:1.7, marginBottom:28 }}>{p.desc}</p>
            <div style={{ borderTop:'1px solid #1E1E2A', paddingTop:24 }}>
              <div style={{ fontSize:11, fontWeight:600, letterSpacing:'0.08em', textTransform:'uppercase', color:'#5A5A72', marginBottom:14 }}>Outcomes</div>
              <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
                {p.outcomes.map((o, i) => (
                  <div key={i} style={{ display:'flex', alignItems:'center', gap:10 }}>
                    <div style={{ width:6, height:6, borderRadius:'50%', background:'#6C63FF', flexShrink:0 }}></div>
                    <span style={{ fontSize:14, color:'#F0EEF8' }}>{o}</span>
                  </div>
                ))}
              </div>
            </div>
            <button style={{ marginTop:28, fontSize:13, fontWeight:500, color:'#A78BFA', background:'transparent', border:'none', cursor:'pointer', display:'flex', alignItems:'center', gap:6, padding:0 }}>
              Read full case study
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
            </button>
          </div>
        </div>
      </div>
    </section>
  );
};

window.Projects = Projects;
