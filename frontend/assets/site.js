(function () {
  const root = document.documentElement;
  const menuBtn = document.getElementById('menuBtn');
  const navLinks = document.getElementById('navLinks');

  // InstaGrab currently stays dark by design. Light mode can be added later.
  const values = {
    '--bg':'#080a12',
    '--bg2':'#0d1020',
    '--text':'#f7f7fb',
    '--muted':'#9ba5bc',
    '--panel':'#101525'
  };
  Object.entries(values).forEach(([key, value]) => root.style.setProperty(key, value));
  document.body.style.background = '#080a12';

  menuBtn?.addEventListener('click', () => {
    const open = navLinks?.classList.toggle('open') || false;
    menuBtn.setAttribute('aria-expanded', String(open));
    menuBtn.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
  });

  navLinks?.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', () => {
      navLinks.classList.remove('open');
      menuBtn?.setAttribute('aria-expanded', 'false');
      menuBtn?.setAttribute('aria-label', 'Open menu');
    });
  });

  const observer = 'IntersectionObserver' in window
    ? new IntersectionObserver((entries) => entries.forEach((entry) => {
        if (entry.isIntersecting) entry.target.classList.add('in');
      }), { threshold: 0.12 })
    : null;
  document.querySelectorAll('.reveal').forEach((el) => observer ? observer.observe(el) : el.classList.add('in'));
})();
