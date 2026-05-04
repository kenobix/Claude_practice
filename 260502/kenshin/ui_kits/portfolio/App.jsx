// App.jsx — Root app with section routing
const { useState, useEffect, useRef } = React;

function Footer() {
  return (
    <footer style={{ borderTop:'1px solid #1E1E2A', padding:'32px 24px' }}>
      <div style={{ maxWidth:1200, margin:'0 auto', display:'flex', justifyContent:'space-between', alignItems:'center', flexWrap:'wrap', gap:16 }}>
        <div style={{ display:'flex', alignItems:'center', gap:10 }}>
          <svg width="18" height="22" viewBox="0 0 28 32" fill="none">
            <rect x="0" y="0" width="4" height="32" fill="#6C63FF"/>
            <path d="M4 16 L18 0 L24 0 L10 16Z" fill="#6C63FF"/>
            <path d="M4 16 L18 32 L24 32 L10 16Z" fill="#A78BFA"/>
          </svg>
          <span style={{ fontFamily:"'Syne',sans-serif", fontSize:15, fontWeight:700, color:'#F0EEF8', letterSpacing:'-0.02em' }}>kenshin</span>
        </div>
        <span style={{ fontSize:12, color:'#5A5A72' }}>IT Engineer &amp; Business Consultant · {new Date().getFullYear()}</span>
        <div style={{ display:'flex', gap:20 }}>
          {['LinkedIn', 'GitHub', 'Email'].map(l => (
            <a key={l} href="#" style={{ fontSize:13, color:'#5A5A72', textDecoration:'none', transition:'color 150ms ease' }}
              onMouseEnter={e => e.currentTarget.style.color='#F0EEF8'}
              onMouseLeave={e => e.currentTarget.style.color='#5A5A72'}
            >{l}</a>
          ))}
        </div>
      </div>
    </footer>
  );
}

function App() {
  const [section, setSection] = useState('hero');
  const refs = {
    hero: useRef(null),
    services: useRef(null),
    projects: useRef(null),
    about: useRef(null),
    contact: useRef(null),
  };

  const scrollTo = (id) => {
    const el = document.getElementById(id);
    if (el) {
      const top = el.getBoundingClientRect().top + window.scrollY - 64;
      window.scrollTo({ top, behavior: 'smooth' });
    }
    setSection(id);
  };

  useEffect(() => {
    // After initial render, init Lucide icons
    if (window.lucide) window.lucide.createIcons();
  });

  useEffect(() => {
    const observer = new IntersectionObserver(
      entries => {
        entries.forEach(e => { if (e.isIntersecting) setSection(e.target.id); });
      },
      { threshold: 0.3 }
    );
    ['hero','services','projects','about','contact'].forEach(id => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);

  return (
    <div style={{ background:'#0D0D12', minHeight:'100vh' }}>
      <Nav currentSection={section} onNav={scrollTo} />
      <main>
        <Hero onNav={scrollTo} />
        <Services />
        <Projects onNav={scrollTo} />
        <About />
        <Contact />
      </main>
      <Footer />
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
