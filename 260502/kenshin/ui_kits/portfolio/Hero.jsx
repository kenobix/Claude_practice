// Hero.jsx — Landing hero section
const Hero = ({ onNav }) => {
  const sectionStyle = {
    minHeight: '100vh',
    display: 'flex', alignItems: 'center',
    position: 'relative',
    padding: '0 24px',
    overflow: 'hidden',
  };
  const innerStyle = {
    maxWidth: 1200, margin: '0 auto', width: '100%',
    paddingTop: 96,
  };
  const overlineStyle = {
    fontFamily: "'DM Sans', sans-serif",
    fontSize: 12, fontWeight: 600,
    letterSpacing: '0.12em', textTransform: 'uppercase',
    color: '#6C63FF', marginBottom: 20,
    display: 'flex', alignItems: 'center', gap: 8,
  };
  const h1Style = {
    fontFamily: "'Syne', sans-serif",
    fontSize: 'clamp(42px, 6vw, 76px)',
    fontWeight: 700,
    lineHeight: 1.05,
    letterSpacing: '-0.03em',
    color: '#F0EEF8',
    marginBottom: 24,
    maxWidth: 800,
  };
  const accentStyle = {
    background: 'linear-gradient(135deg, #6C63FF, #A78BFA)',
    WebkitBackgroundClip: 'text',
    WebkitTextFillColor: 'transparent',
    backgroundClip: 'text',
  };
  const subtitleStyle = {
    fontFamily: "'DM Sans', sans-serif",
    fontSize: 20, fontWeight: 400,
    lineHeight: 1.6, color: '#8B8A9E',
    maxWidth: 540, marginBottom: 40,
  };
  const ctaRowStyle = {
    display: 'flex', gap: 12, alignItems: 'center', marginBottom: 80,
  };
  const btnPrimary = {
    fontFamily: "'DM Sans', sans-serif",
    fontSize: 15, fontWeight: 500,
    background: '#6C63FF', color: '#fff',
    border: 'none', borderRadius: 6,
    padding: '11px 24px', cursor: 'pointer',
    display: 'flex', alignItems: 'center', gap: 8,
    transition: 'background 150ms ease',
  };
  const btnSecondary = {
    fontFamily: "'DM Sans', sans-serif",
    fontSize: 15, fontWeight: 500,
    background: 'transparent', color: '#8B8A9E',
    border: '1px solid #2A2A38', borderRadius: 6,
    padding: '11px 24px', cursor: 'pointer',
    display: 'flex', alignItems: 'center', gap: 8,
    transition: 'all 150ms ease',
  };
  const statsStyle = {
    display: 'flex', gap: 48, flexWrap: 'wrap',
    paddingTop: 48, borderTop: '1px solid #1E1E2A',
  };
  const statStyle = {
    display: 'flex', flexDirection: 'column', gap: 4,
  };

  // Decorative grid lines
  const gridDecoStyle = {
    position: 'absolute', right: 0, top: '50%',
    transform: 'translateY(-50%)',
    width: '40%', height: '70%',
    backgroundImage: `
      linear-gradient(#2A2A38 1px, transparent 1px),
      linear-gradient(90deg, #2A2A38 1px, transparent 1px)
    `,
    backgroundSize: '40px 40px',
    opacity: 0.3,
    maskImage: 'radial-gradient(ellipse at center, black 20%, transparent 70%)',
    WebkitMaskImage: 'radial-gradient(ellipse at center, black 20%, transparent 70%)',
    pointerEvents: 'none',
  };

  return (
    <section id="hero" style={sectionStyle}>
      <div style={gridDecoStyle}></div>
      <div style={innerStyle}>
        <div style={overlineStyle}>
          <span style={{ display:'inline-block', width: 20, height: 1, background: '#6C63FF' }}></span>
          IT Engineer &amp; Business Consultant
        </div>
        <h1 style={h1Style}>
          Bridging<br/>
          <span style={accentStyle}>technology</span><br/>
          and strategy.
        </h1>
        <p style={subtitleStyle}>
          I translate complex business requirements into systems that actually get built — working at the intersection of IT and organisational thinking.
        </p>
        <div style={ctaRowStyle}>
          <button style={btnPrimary}
            onClick={() => onNav('projects')}
            onMouseEnter={e => e.currentTarget.style.background='#7C74FF'}
            onMouseLeave={e => e.currentTarget.style.background='#6C63FF'}
          >
            View my work
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
          </button>
          <button style={btnSecondary}
            onClick={() => onNav('contact')}
            onMouseEnter={e => { e.currentTarget.style.color='#F0EEF8'; e.currentTarget.style.borderColor='#3D3D52'; }}
            onMouseLeave={e => { e.currentTarget.style.color='#8B8A9E'; e.currentTarget.style.borderColor='#2A2A38'; }}
          >
            Get in touch
          </button>
        </div>
        <div style={statsStyle}>
          {[
            { num: '12+', label: 'Years in IT & consulting' },
            { num: '40+', label: 'Projects delivered' },
            { num: '3', label: 'Industries served' },
          ].map(s => (
            <div key={s.num} style={statStyle}>
              <span style={{ fontFamily:"'Syne',sans-serif", fontSize: 32, fontWeight: 700, color:'#F0EEF8', letterSpacing:'-0.02em' }}>{s.num}</span>
              <span style={{ fontSize: 13, color:'#5A5A72' }}>{s.label}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};

window.Hero = Hero;
