// About.jsx — About / bio section
const About = () => {
  const sectionStyle = {
    padding: '96px 24px',
    borderTop: '1px solid #1E1E2A',
    background: '#16161F',
  };
  const innerStyle = {
    maxWidth: 1200, margin: '0 auto',
    display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 80,
    alignItems: 'center',
  };
  const tagStyle = {
    display: 'inline-flex', alignItems: 'center', gap: 6,
    fontSize: 11, fontWeight: 600, letterSpacing: '0.08em',
    padding: '4px 10px', borderRadius: 4,
    background: '#252533', color: '#8B8A9E',
    border: '1px solid #2A2A38',
  };

  const skills = ['Requirements definition', 'Enterprise architecture', 'Stakeholder management', 'Change management', 'Business analysis', 'IT strategy', 'Agile / scrum', 'SQL & data modelling'];

  return (
    <section id="about" style={sectionStyle}>
      <div style={innerStyle}>
        <div>
          <div style={{ fontFamily:"'DM Sans',sans-serif", fontSize:11, fontWeight:600, letterSpacing:'0.12em', textTransform:'uppercase', color:'#5A5A72', marginBottom:12 }}>Background</div>
          <h2 style={{ fontFamily:"'Syne',sans-serif", fontSize:'clamp(28px,3.5vw,40px)', fontWeight:700, letterSpacing:'-0.02em', color:'#F0EEF8', lineHeight:1.15, marginBottom:24 }}>
            About me
          </h2>
          <p style={{ fontSize:16, color:'#8B8A9E', lineHeight:1.7, marginBottom:16 }}>
            I'm Kenshin — an IT engineer turned business consultant with over a decade of experience helping organisations solve problems at the intersection of technology and operations.
          </p>
          <p style={{ fontSize:16, color:'#8B8A9E', lineHeight:1.7, marginBottom:16 }}>
            I've led projects across manufacturing, logistics, and financial services — from greenfield system builds to complex legacy migrations. My background in engineering means I can hold a technical conversation; my consulting experience means I can translate it for a boardroom.
          </p>
          <p style={{ fontSize:16, color:'#8B8A9E', lineHeight:1.7, marginBottom:32 }}>
            Let's define the problem before we talk about the solution.
          </p>
          <div style={{ display:'flex', gap:8, flexWrap:'wrap' }}>
            <a href="#" style={{ display:'flex', alignItems:'center', gap:6, fontSize:13, fontWeight:500, color:'#A78BFA', textDecoration:'none' }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 8a6 6 0 016 6v7h-4v-7a2 2 0 00-2-2 2 2 0 00-2 2v7h-4v-7a6 6 0 016-6zM2 9h4v12H2z"/><circle cx="4" cy="4" r="2"/></svg>
              LinkedIn
            </a>
            <span style={{ color:'#2A2A38' }}>·</span>
            <a href="#" style={{ display:'flex', alignItems:'center', gap:6, fontSize:13, fontWeight:500, color:'#A78BFA', textDecoration:'none' }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><path d="M8 21h8M12 17v4"/></svg>
              Resume (PDF)
            </a>
          </div>
        </div>
        <div>
          <div style={{ fontFamily:"'DM Sans',sans-serif", fontSize:11, fontWeight:600, letterSpacing:'0.12em', textTransform:'uppercase', color:'#5A5A72', marginBottom:20 }}>Skills & expertise</div>
          <div style={{ display:'flex', flexWrap:'wrap', gap:8 }}>
            {skills.map((s, i) => (
              <span key={i} style={tagStyle}>{s}</span>
            ))}
          </div>
          <div style={{ marginTop:40, padding:24, background:'#1E1E2A', borderRadius:8, border:'1px solid #2A2A38' }}>
            <div style={{ fontSize:11, fontWeight:600, letterSpacing:'0.1em', textTransform:'uppercase', color:'#5A5A72', marginBottom:16 }}>Currently available for</div>
            {['Project-based consulting', 'Interim IT management', 'Advisory & mentoring'].map((item, i) => (
              <div key={i} style={{ display:'flex', alignItems:'center', gap:10, marginBottom:10 }}>
                <div style={{ width:6, height:6, borderRadius:'50%', background:'#34D399', flexShrink:0 }}></div>
                <span style={{ fontSize:14, color:'#F0EEF8' }}>{item}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
};

window.About = About;
