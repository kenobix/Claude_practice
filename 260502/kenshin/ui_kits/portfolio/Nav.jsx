// Nav.jsx — Top navigation bar
const { useState, useEffect } = React;

function Nav({ currentSection, onNav }) {
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40);
    window.addEventListener('scroll', onScroll);
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  const links = ['Services', 'Projects', 'About', 'Contact'];

  const navStyle = {
    position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100,
    borderBottom: scrolled ? '1px solid #2A2A38' : '1px solid transparent',
    background: scrolled ? 'rgba(13,13,18,0.92)' : 'transparent',
    backdropFilter: scrolled ? 'blur(12px)' : 'none',
    transition: 'all 250ms cubic-bezier(0.4,0,0.2,1)',
  };
  const innerStyle = {
    maxWidth: 1200, margin: '0 auto',
    padding: '0 24px',
    height: 64,
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  };
  const logoStyle = {
    display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer',
  };
  const linksStyle = {
    display: 'flex', alignItems: 'center', gap: 4,
  };
  const linkStyle = (active) => ({
    fontFamily: "'DM Sans', sans-serif",
    fontSize: 14, fontWeight: 500,
    color: active ? '#F0EEF8' : '#8B8A9E',
    padding: '6px 12px', borderRadius: 6,
    background: active ? '#1E1E2A' : 'transparent',
    cursor: 'pointer',
    transition: 'all 150ms ease',
    border: 'none',
  });
  const ctaStyle = {
    fontFamily: "'DM Sans', sans-serif",
    fontSize: 14, fontWeight: 500,
    background: '#6C63FF', color: '#fff',
    border: 'none', borderRadius: 6,
    padding: '7px 16px', cursor: 'pointer',
    marginLeft: 8,
    transition: 'background 150ms ease',
  };

  return (
    <nav style={navStyle}>
      <div style={innerStyle}>
        {/* Logo */}
        <div style={logoStyle} onClick={() => onNav('hero')}>
          <svg width="22" height="26" viewBox="0 0 28 32" fill="none">
            <rect x="0" y="0" width="4" height="32" fill="#6C63FF"/>
            <path d="M4 16 L18 0 L24 0 L10 16Z" fill="#6C63FF"/>
            <path d="M4 16 L18 32 L24 32 L10 16Z" fill="#A78BFA"/>
          </svg>
          <span style={{ fontFamily: "'Syne', sans-serif", fontSize: 18, fontWeight: 700, letterSpacing: '-0.02em', color: '#F0EEF8' }}>kenshin</span>
        </div>

        {/* Desktop links */}
        <div style={linksStyle}>
          {links.map(l => (
            <button key={l} style={linkStyle(currentSection === l.toLowerCase())}
              onClick={() => onNav(l.toLowerCase())}
              onMouseEnter={e => { if (currentSection !== l.toLowerCase()) { e.currentTarget.style.color = '#F0EEF8'; e.currentTarget.style.background = '#16161F'; }}}
              onMouseLeave={e => { if (currentSection !== l.toLowerCase()) { e.currentTarget.style.color = '#8B8A9E'; e.currentTarget.style.background = 'transparent'; }}}
            >{l}</button>
          ))}
          <button style={ctaStyle}
            onMouseEnter={e => e.currentTarget.style.background = '#7C74FF'}
            onMouseLeave={e => e.currentTarget.style.background = '#6C63FF'}
            onClick={() => onNav('contact')}
          >Get in touch</button>
        </div>
      </div>
    </nav>
  );
}

window.Nav = Nav;
