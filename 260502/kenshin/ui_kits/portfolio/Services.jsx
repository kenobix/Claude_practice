// Services.jsx — Services / expertise section
const Services = () => {
  const sectionStyle = {
    padding: '96px 24px',
    borderTop: '1px solid #1E1E2A',
    position: 'relative',
  };
  const innerStyle = {
    maxWidth: 1200, margin: '0 auto',
  };
  const headerStyle = {
    display: 'flex', justifyContent: 'space-between',
    alignItems: 'flex-end', marginBottom: 56, flexWrap: 'wrap', gap: 24,
  };
  const gridStyle = {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
    gap: 16,
  };
  const cardStyle = (hovered) => ({
    background: hovered ? '#1E1E2A' : '#16161F',
    border: `1px solid ${hovered ? '#3D3D52' : '#2A2A38'}`,
    borderRadius: 8,
    padding: 28,
    cursor: 'default',
    transition: 'all 200ms cubic-bezier(0.4,0,0.2,1)',
    transform: hovered ? 'translateY(-2px)' : 'none',
    boxShadow: hovered ? '0 4px 16px rgba(0,0,0,0.4)' : '0 1px 3px rgba(0,0,0,0.3)',
  });

  const [hovered, setHovered] = React.useState(null);

  const services = [
    { icon: 'layers', title: 'Requirements definition', desc: 'Structured discovery and gap analysis that aligns stakeholders and eliminates ambiguity before build begins.', tag: 'Strategy' },
    { icon: 'git-branch', title: 'System architecture', desc: 'Technical design for complex systems — from data flows to integration patterns, documented for engineers and executives.', tag: 'Technical' },
    { icon: 'users', title: 'Organisational change', desc: 'Guiding teams through technology transitions with communication frameworks and structured change programmes.', tag: 'People' },
    { icon: 'bar-chart-2', title: 'Business analysis', desc: 'Process mapping, KPI definition, and data-driven recommendations grounded in operational reality.', tag: 'Analysis' },
    { icon: 'map', title: 'Roadmap planning', desc: 'Turning strategy into sequenced, deliverable milestones with clear ownership and success criteria.', tag: 'Strategy' },
    { icon: 'cpu', title: 'IT due diligence', desc: 'Technical assessments for M&A, procurement, and vendor selection — risk and opportunity in plain language.', tag: 'Technical' },
  ];

  const tagColors = {
    Strategy: '#6C63FF', Technical: '#34D399', People: '#FBBF24', Analysis: '#60A5FA',
  };

  return (
    <section id="services" style={sectionStyle}>
      <div style={innerStyle}>
        <div style={headerStyle}>
          <div>
            <div style={{ fontFamily:"'DM Sans',sans-serif", fontSize:11, fontWeight:600, letterSpacing:'0.12em', textTransform:'uppercase', color:'#5A5A72', marginBottom:12 }}>What I do</div>
            <h2 style={{ fontFamily:"'Syne',sans-serif", fontSize:'clamp(28px,4vw,44px)', fontWeight:700, letterSpacing:'-0.02em', color:'#F0EEF8', lineHeight:1.15 }}>Services</h2>
          </div>
          <p style={{ fontSize:15, color:'#8B8A9E', maxWidth:320, lineHeight:1.65 }}>
            From initial discovery to final delivery — I work across the full arc of technology-enabled change.
          </p>
        </div>
        <div style={gridStyle}>
          {services.map((s, i) => (
            <div key={i} style={cardStyle(hovered === i)}
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
            >
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:20 }}>
                <div style={{ width:36, height:36, borderRadius:6, background:'#252533', display:'flex', alignItems:'center', justifyContent:'center' }}>
                  <i data-lucide={s.icon} style={{ width:16, height:16, color:'#6C63FF' }}></i>
                </div>
                <span style={{ fontSize:10, fontWeight:600, letterSpacing:'0.06em', color: tagColors[s.tag] || '#8B8A9E', background:'rgba(108,99,255,0.08)', padding:'3px 8px', borderRadius:4 }}>{s.tag}</span>
              </div>
              <h3 style={{ fontFamily:"'Syne',sans-serif", fontSize:16, fontWeight:600, color:'#F0EEF8', marginBottom:10, lineHeight:1.3 }}>{s.title}</h3>
              <p style={{ fontSize:14, color:'#8B8A9E', lineHeight:1.6 }}>{s.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};

window.Services = Services;
