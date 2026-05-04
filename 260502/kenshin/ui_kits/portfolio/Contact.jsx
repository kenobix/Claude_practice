// Contact.jsx — Contact / CTA section
const Contact = () => {
  const [form, setForm] = React.useState({ name: '', email: '', message: '', type: 'Strategy & Requirements' });
  const [sent, setSent] = React.useState(false);

  const sectionStyle = {
    padding: '96px 24px',
    borderTop: '1px solid #1E1E2A',
  };
  const innerStyle = {
    maxWidth: 640, margin: '0 auto', textAlign: 'center',
  };
  const inputStyle = {
    width: '100%', background: '#16161F',
    border: '1px solid #2A2A38', borderRadius: 6,
    padding: '10px 14px',
    fontFamily: "'DM Sans', sans-serif", fontSize: 15, color: '#F0EEF8',
    outline: 'none', transition: 'border-color 150ms ease',
    marginBottom: 12,
  };
  const labelStyle = {
    display: 'block', fontSize: 13, fontWeight: 500,
    color: '#8B8A9E', marginBottom: 6, textAlign: 'left',
  };
  const btnStyle = {
    width: '100%', padding: '12px 24px',
    background: 'linear-gradient(135deg, #6C63FF, #7C74FF)',
    color: '#fff', border: 'none', borderRadius: 6,
    fontFamily: "'DM Sans', sans-serif", fontSize: 15, fontWeight: 600,
    cursor: 'pointer', transition: 'opacity 150ms ease',
    marginTop: 8,
  };

  if (sent) return (
    <section id="contact" style={sectionStyle}>
      <div style={innerStyle}>
        <div style={{ width:56, height:56, borderRadius:'50%', background:'#0F2E24', border:'1px solid #34D399', display:'flex', alignItems:'center', justifyContent:'center', margin:'0 auto 20px' }}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#34D399" strokeWidth="2"><polyline points="20 6 9 17 4 12"/></svg>
        </div>
        <h3 style={{ fontFamily:"'Syne',sans-serif", fontSize:24, fontWeight:700, color:'#F0EEF8', marginBottom:12 }}>Message sent</h3>
        <p style={{ fontSize:15, color:'#8B8A9E', lineHeight:1.6 }}>I'll be in touch within one business day.</p>
      </div>
    </section>
  );

  return (
    <section id="contact" style={sectionStyle}>
      <div style={innerStyle}>
        <div style={{ fontFamily:"'DM Sans',sans-serif", fontSize:11, fontWeight:600, letterSpacing:'0.12em', textTransform:'uppercase', color:'#5A5A72', marginBottom:12 }}>Get in touch</div>
        <h2 style={{ fontFamily:"'Syne',sans-serif", fontSize:'clamp(28px,4vw,44px)', fontWeight:700, letterSpacing:'-0.02em', color:'#F0EEF8', lineHeight:1.15, marginBottom:16 }}>
          Let's talk about your project
        </h2>
        <p style={{ fontSize:16, color:'#8B8A9E', lineHeight:1.7, marginBottom:48 }}>
          Whether you have a clear brief or a half-formed idea, I'm happy to have an exploratory conversation.
        </p>
        <form onSubmit={e => { e.preventDefault(); setSent(true); }} style={{ textAlign:'left' }}>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'0 16px' }}>
            <div>
              <label style={labelStyle}>Name</label>
              <input type="text" style={inputStyle} placeholder="Your name" value={form.name}
                onChange={e => setForm(f => ({...f, name: e.target.value}))}
                onFocus={e => e.target.style.borderColor='#6C63FF'}
                onBlur={e => e.target.style.borderColor='#2A2A38'}
              />
            </div>
            <div>
              <label style={labelStyle}>Email</label>
              <input type="email" style={inputStyle} placeholder="you@company.com" value={form.email}
                onChange={e => setForm(f => ({...f, email: e.target.value}))}
                onFocus={e => e.target.style.borderColor='#6C63FF'}
                onBlur={e => e.target.style.borderColor='#2A2A38'}
              />
            </div>
          </div>
          <div>
            <label style={labelStyle}>Engagement type</label>
            <select style={{...inputStyle, cursor:'pointer'}} value={form.type}
              onChange={e => setForm(f => ({...f, type: e.target.value}))}
              onFocus={e => e.target.style.borderColor='#6C63FF'}
              onBlur={e => e.target.style.borderColor='#2A2A38'}
            >
              <option>Strategy &amp; Requirements</option>
              <option>System Architecture</option>
              <option>Change Management</option>
              <option>IT Due Diligence</option>
              <option>Other / Not sure yet</option>
            </select>
          </div>
          <div>
            <label style={labelStyle}>Message</label>
            <textarea rows={4} style={{...inputStyle, resize:'vertical'}} placeholder="Tell me about your challenge…" value={form.message}
              onChange={e => setForm(f => ({...f, message: e.target.value}))}
              onFocus={e => e.target.style.borderColor='#6C63FF'}
              onBlur={e => e.target.style.borderColor='#2A2A38'}
            />
          </div>
          <button type="submit" style={btnStyle}
            onMouseEnter={e => e.currentTarget.style.opacity='0.9'}
            onMouseLeave={e => e.currentTarget.style.opacity='1'}
          >Send message</button>
        </form>
      </div>
    </section>
  );
};

window.Contact = Contact;
