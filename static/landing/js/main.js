/**
 * ProcureSphere 360 — Main Site JavaScript
 * Replaces all React/Framer-motion interactive behaviors with vanilla JS.
 * Organized into self-contained IIFE modules.
 */
(function () {
  'use strict';

  /* ─────────────────────────────────────────────
     1. SCROLL PROGRESS BAR
  ──────────────────────────────────────────────*/
  (function scrollProgress() {
    const bar = document.getElementById('scroll-progress-bar');
    if (!bar) return;
    window.addEventListener('scroll', () => {
      const scrolled = window.scrollY / (document.documentElement.scrollHeight - window.innerHeight);
      bar.style.width = Math.min(scrolled * 100, 100) + '%';
    }, { passive: true });
  })();

  /* ─────────────────────────────────────────────
     2. NAVBAR: SCROLL DETECTION + DROPDOWN
  ──────────────────────────────────────────────*/
  (function navbar() {
    const nav = document.getElementById('site-navbar');
    if (!nav) return;

    // Scroll shadow
    window.addEventListener('scroll', () => {
      nav.classList.toggle('is-scrolled', window.scrollY > 20);
    }, { passive: true });

    // Dropdowns
    document.querySelectorAll('.nav-item.has-dropdown').forEach(item => {
      const link = item.querySelector('.nav-link');
      const menu = item.querySelector('.nav-dropdown-menu');
      if (!link || !menu) return;

      let timeout;
      const show = () => {
        clearTimeout(timeout);
        menu.classList.add('is-open');
        const chevron = link.querySelector('.nav-chevron');
        if (chevron) chevron.style.transform = 'rotate(180deg)';
      };
      const hide = () => {
        timeout = setTimeout(() => {
          menu.classList.remove('is-open');
          const chevron = link.querySelector('.nav-chevron');
          if (chevron) chevron.style.transform = '';
        }, 120);
      };
      item.addEventListener('mouseenter', show);
      item.addEventListener('mouseleave', hide);
      menu.addEventListener('mouseenter', show);
      menu.addEventListener('mouseleave', hide);
    });
  })();

  /* ─────────────────────────────────────────────
     3. MOBILE OVERLAY MENU TOGGLE
  ──────────────────────────────────────────────*/
  (function overlayMenu() {
    const toggle = document.getElementById('mobile-menu-toggle');
    const menu = document.getElementById('overlay-menu');
    const hamIcon = document.getElementById('hamburger-icon');
    const closeIcon = document.getElementById('close-icon');
    if (!toggle || !menu) return;

    let isOpen = false;

    function openMenu() {
      isOpen = true;
      menu.classList.add('is-open');
      menu.setAttribute('aria-hidden', 'false');
      toggle.setAttribute('aria-expanded', 'true');
      document.body.style.overflow = 'hidden';
      if (hamIcon) hamIcon.style.display = 'none';
      if (closeIcon) closeIcon.style.display = '';
    }

    function closeMenu() {
      isOpen = false;
      menu.classList.remove('is-open');
      menu.setAttribute('aria-hidden', 'true');
      toggle.setAttribute('aria-expanded', 'false');
      document.body.style.overflow = '';
      if (hamIcon) hamIcon.style.display = '';
      if (closeIcon) closeIcon.style.display = 'none';
    }

    toggle.addEventListener('click', () => isOpen ? closeMenu() : openMenu());

    // Close on backdrop click
    menu.addEventListener('click', e => {
      if (e.target === menu || e.target.classList.contains('overlay-menu-bg')) closeMenu();
    });

    // Close on ESC
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && isOpen) closeMenu();
    });

    // Close on nav item click (SPA style navigation)
    menu.querySelectorAll('.overlay-menu-item').forEach(item => {
      item.addEventListener('click', () => closeMenu());
    });

    // Overlay login button
    const overlayLoginBtn = document.getElementById('overlay-login-btn');
    if (overlayLoginBtn) {
      overlayLoginBtn.addEventListener('click', () => {
        closeMenu();
        openLoginModal();
      });
    }
  })();

  /* ─────────────────────────────────────────────
     4. LOGIN MODAL
  ──────────────────────────────────────────────*/
  function openLoginModal() {
    const modal = document.getElementById('login-modal');
    if (!modal) return;
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    // Reset to normal state
    setLoginCharState('normal');
    const errorMsg = document.getElementById('login-error-msg');
    if (errorMsg) { errorMsg.style.display = 'none'; errorMsg.textContent = ''; }
  }

  function closeLoginModal() {
    const modal = document.getElementById('login-modal');
    if (!modal) return;
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
  }

  function setLoginCharState(state) {
    const img = document.getElementById('login-char-img');
    if (!img) return;
    const states = {
      normal: '/static/landing/login-states/normal.webp',
      typing: '/static/landing/login-states/password-typing.webp',
      failed: '/static/landing/login-states/login-failed.webp',
      success: '/static/landing/login-states/login-success.webp',
    };
    if (states[state]) img.src = states[state];
  }

  (function loginModal() {
    // Trigger buttons
    document.querySelectorAll('#login-btn, #overlay-login-btn').forEach(btn => {
      btn.addEventListener('click', openLoginModal);
    });

    // Close button
    const closeBtn = document.getElementById('login-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', closeLoginModal);

    // Backdrop click
    const modal = document.getElementById('login-modal');
    if (modal) {
      modal.addEventListener('click', e => {
        if (e.target === modal) closeLoginModal();
      });
    }

    // ESC key
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        const m = document.getElementById('login-modal');
        if (m && m.classList.contains('is-open')) closeLoginModal();
      }
    });

    // Password field interaction → character state
    const pwInput = document.getElementById('login-password');
    if (pwInput) {
      pwInput.addEventListener('focus', () => setLoginCharState('typing'));
      pwInput.addEventListener('blur', () => {
        const form = document.getElementById('login-form');
        if (form && !form.dataset.loginState) setLoginCharState('normal');
      });
    }

    // Toggle password visibility
    const togglePw = document.getElementById('toggle-password');
    if (togglePw && pwInput) {
      togglePw.addEventListener('click', () => {
        pwInput.type = pwInput.type === 'password' ? 'text' : 'password';
      });
    }

    // Form submit — validates fields client-side then lets the native
    // HTML form POST to /login/ (the real ERP authentication endpoint).
    // The spinner gives visual feedback while the page navigates.
    const form = document.getElementById('login-form');
    if (form) {
      form.addEventListener('submit', (e) => {
        const emailVal = form.querySelector('#login-userid')?.value.trim();
        const pwVal    = form.querySelector('#login-password')?.value.trim();
        const errorEl  = document.getElementById('login-error-msg');

        if (!emailVal || !pwVal) {
          e.preventDefault();
          setLoginCharState('failed');
          if (errorEl) {
            errorEl.textContent = 'Please enter both email and password.';
            errorEl.style.display = 'flex';
          }
          return;
        }

        // Show loading state while the page navigates to /login/
        const submitBtn = document.getElementById('login-submit-btn');
        const btnText   = document.getElementById('login-btn-text');
        const spinner   = document.getElementById('login-btn-spinner');
        if (submitBtn) submitBtn.disabled = true;
        if (btnText)   btnText.style.display = 'none';
        if (spinner)   spinner.style.display = 'flex';
        if (errorEl)   errorEl.style.display = 'none';
        // Native form POST continues — no e.preventDefault()
      });
    }
  })();

  /* ─────────────────────────────────────────────
     5. LEGAL MODAL
  ──────────────────────────────────────────────*/
  (function legalModal() {
    const modal = document.getElementById('legal-modal');
    const closeBtn = document.getElementById('legal-close-btn');
    const tabs = document.querySelectorAll('.legal-tab');

    function openLegal(tab) {
      if (!modal) return;
      modal.classList.add('is-open');
      modal.setAttribute('aria-hidden', 'false');
      document.body.style.overflow = 'hidden';
      if (tab) switchLegalTab(tab);
    }

    function closeLegal() {
      if (!modal) return;
      modal.classList.remove('is-open');
      modal.setAttribute('aria-hidden', 'true');
      document.body.style.overflow = '';
    }

    function switchLegalTab(tabName) {
      document.querySelectorAll('.legal-tab-content').forEach(c => c.classList.remove('active'));
      document.querySelectorAll('.legal-tab').forEach(t => t.classList.remove('active'));
      const content = document.getElementById(tabName + '-content');
      const tabBtn = document.getElementById(tabName + '-tab');
      if (content) content.classList.add('active');
      if (tabBtn) tabBtn.classList.add('active');
    }

    // Tab buttons
    tabs.forEach(tab => {
      tab.addEventListener('click', () => switchLegalTab(tab.dataset.tab));
    });

    // Open from footer links
    document.querySelectorAll('.open-privacy-modal').forEach(el => {
      el.addEventListener('click', e => { e.preventDefault(); openLegal('privacy'); });
    });
    document.querySelectorAll('.open-terms-modal').forEach(el => {
      el.addEventListener('click', e => { e.preventDefault(); openLegal('terms'); });
    });

    if (closeBtn) closeBtn.addEventListener('click', closeLegal);
    if (modal) {
      modal.addEventListener('click', e => {
        if (e.target === modal) closeLegal();
      });
    }
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && modal?.classList.contains('is-open')) closeLegal();
    });
  })();

  /* ─────────────────────────────────────────────
     6. SCROLL-BASED ANIMATIONS (IntersectionObserver)
  ──────────────────────────────────────────────*/
  (function scrollAnimations() {
    const style = document.createElement('style');
    style.textContent = `
      .animate-on-scroll { opacity: 0; transform: translateY(24px); transition: opacity 0.55s cubic-bezier(0.16,1,0.3,1), transform 0.55s cubic-bezier(0.16,1,0.3,1); }
      .animate-on-scroll.in-view { opacity: 1; transform: none; }
    `;
    document.head.appendChild(style);

    const observer = new IntersectionObserver(
      entries => entries.forEach(e => {
        if (e.isIntersecting) { e.target.classList.add('in-view'); observer.unobserve(e.target); }
      }),
      { threshold: 0.1, rootMargin: '0px 0px -40px 0px' }
    );

    document.querySelectorAll('.animate-on-scroll').forEach(el => observer.observe(el));
  })();

  /* ─────────────────────────────────────────────
     7. FAQ ACCORDION
  ──────────────────────────────────────────────*/
  window.toggleFAQ = function (index) {
    const answer = document.getElementById('faq-answer-' + index);
    const icon = document.getElementById('faq-icon-' + index);
    const btn = document.getElementById('faq-btn-' + index);
    if (!answer || !icon) return;

    const isOpen = answer.style.display !== 'none';

    // Close all others
    document.querySelectorAll('[id^="faq-answer-"]').forEach(el => {
      el.style.display = 'none';
    });
    document.querySelectorAll('[id^="faq-icon-"]').forEach(el => {
      el.style.background = '#f0f4ff';
      el.style.color = '#2962ff';
      el.style.transform = 'rotate(0)';
    });
    document.querySelectorAll('[id^="faq-btn-"]').forEach(el => {
      el.setAttribute('aria-expanded', 'false');
    });

    if (!isOpen) {
      answer.style.display = 'block';
      icon.style.background = '#2962ff';
      icon.style.color = '#fff';
      icon.style.transform = 'rotate(135deg)';
      if (btn) btn.setAttribute('aria-expanded', 'true');
    }
  };

  /* ─────────────────────────────────────────────
     8. PLATFORM MODULE SELECTION (Home)
  ──────────────────────────────────────────────*/
  window.selectPlatformModule = function (moduleId) {
    // Hide all detail panels
    document.querySelectorAll('.module-detail-content').forEach(el => el.style.display = 'none');
    // Show selected
    const detail = document.getElementById('module-detail-' + moduleId);
    if (detail) detail.style.display = 'flex';

    // Update card states
    document.querySelectorAll('[data-module]').forEach(card => {
      const isActive = parseInt(card.dataset.module) === moduleId;
      card.style.outline = isActive ? '3.5px solid ' + (card.dataset.accent || '#00d2ff') : '1.5px solid rgba(15,27,45,0.12)';
      card.style.outlineOffset = '2px';
      card.style.transform = isActive ? 'scale(1.025)' : '';
      const color = card.dataset.color || '#0052cc';
      card.style.boxShadow = isActive
        ? `0 22px 48px -8px ${color}66, 0 8px 22px rgba(15,27,45,0.22)`
        : '0 12px 30px -6px rgba(15,27,45,0.24), 0 4px 14px rgba(15,27,45,0.12)';

      // Update tap cue text
      const tapCue = card.querySelector('.card-tap-cue span');
      if (tapCue) {
        tapCue.textContent = isActive ? '● Viewing Details →' : 'Hover to view details ↗';
        tapCue.style.fontWeight = isActive ? '750' : '600';
      }
    });

    // Update accent stripe
    const stripe = document.getElementById('module-accent-stripe');
    if (stripe) {
      const card = document.querySelector(`[data-module="${moduleId}"]`);
      if (card) stripe.style.background = `linear-gradient(90deg,${card.dataset.color},${card.dataset.accent})`;
    }

    // Update active label
    const label = document.getElementById('active-module-label');
    if (label) {
      const card = document.querySelector(`[data-module="${moduleId}"]`);
      if (card) label.textContent = card.dataset.name + ' selected →';
    }
  };

  /* ─────────────────────────────────────────────
     9. MODULES STACK SECTION (hover detail)
  ──────────────────────────────────────────────*/
  (function modulesStack() {
    const display = document.getElementById('module-detail-display');
    if (!display) return;

    const moduleData = [
      { name: 'Vendor Onboarding & KYC', detail: 'Turn supplier intake into a clear, reviewable path.', state: 'KYC REVIEW', category: 'SOURCING', color: '#2456D6' },
      { name: 'Vendor Master & Risk', detail: 'Keep supplier identity, risk and ownership in view.', state: 'ACTIVE', category: 'GOVERNANCE', color: '#8b5cf6' },
      { name: 'Purchase Requisition', detail: 'Capture intent with policy and budget context.', state: 'MANAGER REVIEW', category: 'PURCHASING', color: '#10b981' },
      { name: 'Approval Policy Engine', detail: 'Route decisions to the right owner at the right time.', state: 'BUDGET REVIEW', category: 'GOVERNANCE', color: '#f59e0b' },
      { name: 'RFQ / RFP Sourcing', detail: 'Create a transparent path from need to market.', state: 'BID WINDOW', category: 'SOURCING', color: '#2456D6' },
      { name: 'Vendor Bid Portal', detail: 'Keep submissions structured and comparable.', state: 'PUBLISHED', category: 'SOURCING', color: '#ec4899' },
      { name: 'Evaluation & Award', detail: 'Turn technical and commercial review into a decision.', state: 'AWARD APPROVAL', category: 'SOURCING', color: '#06b6d4' },
      { name: 'Purchase Orders & Amendments', detail: 'Make committed intent visible to every team.', state: 'ACKNOWLEDGED', category: 'PURCHASING', color: '#10b981' },
      { name: 'Receipt & Inspection', detail: 'Connect arrival, inspection and acceptance.', state: 'PARTIAL RECEIPT', category: 'PURCHASING', color: '#f59e0b' },
      { name: 'Invoice & 3-Way Match', detail: 'Resolve exceptions before they become delays.', state: '3-WAY MATCH', category: 'FINANCE', color: '#8b5cf6' },
      { name: 'Contract Lifecycle', detail: 'Keep obligations and renewal decisions visible.', state: 'RENEWAL DUE', category: 'GOVERNANCE', color: '#ef4444' },
      { name: 'Budget & Spend Control', detail: 'Show how a decision changes the financial picture.', state: 'RESERVED', category: 'FINANCE', color: '#2456D6' },
      { name: 'Supplier Performance', detail: 'Make health and collaboration measurable.', state: 'ACTIVE', category: 'SOURCING', color: '#06b6d4' },
      { name: 'Reports & Audit Trail', detail: 'Give every important action a structured place.', state: 'ACTIVE', category: 'GOVERNANCE', color: '#ec4899' },
    ];

    window.selectModule = function (index) {
      const m = moduleData[index];
      if (!m || !display) return;

      // Update all row highlights
      document.querySelectorAll('.module-stack-item').forEach((row, i) => {
        const isActive = i === index;
        row.style.background = isActive ? m.color + '10' : 'transparent';
        row.style.border = isActive ? `1.5px solid ${m.color}60` : '1.5px solid transparent';
        const badge = row.querySelector('.module-num-badge');
        if (badge) {
          badge.style.background = isActive ? m.color : '#eff6ff';
          badge.style.color = isActive ? '#fff' : '#1d68f6';
        }
      });

      // Update detail panel
      display.innerHTML = `
        <div style="padding:8px;height:100%;">
          <div style="display:flex;align-items:center;gap:14px;margin-bottom:20px;">
            <div style="width:48px;height:48px;border-radius:12px;background:${m.color}18;color:${m.color};display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:800;font-family:'DM Mono',monospace;flex-shrink:0;">${String(index + 1).padStart(2, '0')}</div>
            <div>
              <span style="font-size:11px;font-weight:800;color:${m.color};letter-spacing:0.1em;text-transform:uppercase;font-family:'DM Mono',monospace;display:block;margin-bottom:3px;">${m.category}</span>
              <h3 style="font-family:'Playfair Display',Georgia,serif;font-size:22px;color:#0f172a;margin:0;font-weight:700;line-height:1.2;">${m.name}</h3>
            </div>
          </div>
          <p style="font-size:16px;color:#334155;line-height:1.65;margin:0 0 20px;font-weight:500;">${m.detail}</p>
          <div style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px;background:${m.color}10;border:1.5px solid ${m.color}30;border-radius:12px;">
            <span style="font-size:14px;font-weight:700;color:#1e293b;">Current State</span>
            <span style="font-size:12px;font-weight:800;color:${m.color};background:${m.color}18;padding:5px 12px;border-radius:6px;font-family:'DM Mono',monospace;border:1px solid ${m.color}40;">● ${m.state}</span>
          </div>
        </div>`;
    };

    // Initialize hover on items
    document.querySelectorAll('.module-stack-item').forEach((row, i) => {
      row.addEventListener('mouseenter', () => window.selectModule(i));
    });
  })();

  /* ─────────────────────────────────────────────
     10. TRUST CARDS INTERACTION (Uiverse style)
  ──────────────────────────────────────────────*/
  window.toggleTrustCard = function (index) {
    const card = document.getElementById('trust-card-' + index);
    if (!card) return;
    const isExpanded = card.getAttribute('aria-expanded') === 'true';
    card.setAttribute('aria-expanded', String(!isExpanded));
    card.classList.toggle('is-expanded', !isExpanded);
  };

  /* ─────────────────────────────────────────────
     11. ROLE SWITCHER
  ──────────────────────────────────────────────*/
  window.selectRole = function (roleKey) {
    // Hide all dashboard panels (use class, not id-prefix — the wrapper also has id starting with "role-interface-")
    document.querySelectorAll('.role-interface').forEach(el => el.style.display = 'none');
    // Show selected
    const iface = document.getElementById('role-interface-' + roleKey);
    if (iface) iface.style.display = 'grid';

    // Update button states
    document.querySelectorAll('[id^="role-btn-"]').forEach(btn => {
      const isActive = btn.id === 'role-btn-' + roleKey;
      btn.style.opacity = isActive ? '1' : '0.7';
      btn.setAttribute('aria-selected', String(isActive));

      const circle = btn.querySelector('.role-avatar-circle');
      if (circle) {
        circle.style.background = isActive ? '#f0f4ff' : 'transparent';
        circle.classList.toggle('is-active', isActive);
      }
      const label = btn.querySelector('span:last-child');
      if (label) {
        label.style.fontWeight = isActive ? '600' : '500';
        label.style.color = isActive ? '#0f1b2d' : '#64748b';
      }

      // Swap avatar image
      const key = btn.id.replace('role-btn-', '');
      const img = btn.querySelector('img');
      if (img) {
        img.src = `/static/landing/roles/${key.toLowerCase()}/${isActive ? 'active' : 'idle'}.webp`;
      }
    });
  };

  /* ─────────────────────────────────────────────
     12. BRAND PILLS MARQUEE (auto-scroll)
  ──────────────────────────────────────────────*/
  (function marquee() {
    const track = document.querySelector('.brand-pills-track');
    if (!track) return;
    // CSS animation handles this via @keyframes scroll in global.css
    // but we add a pause-on-hover behavior
    track.addEventListener('mouseenter', () => track.style.animationPlayState = 'paused');
    track.addEventListener('mouseleave', () => track.style.animationPlayState = 'running');
  })();

  /* ─────────────────────────────────────────────
     13. HERO VIDEO AUTOPLAY
  ──────────────────────────────────────────────*/
  (function heroVideo() {
    const video = document.getElementById('hero-video');
    if (!video) return;
    video.muted = true;
    video.defaultMuted = true;
    video.playsInline = true;
    video.loop = true;

    const tryPlay = () => {
      if (video.paused) video.play().catch(() => {});
    };

    video.addEventListener('canplay', tryPlay);
    video.addEventListener('loadeddata', tryPlay);

    const observer = new IntersectionObserver(entries => {
      entries.forEach(e => {
        if (e.isIntersecting) tryPlay();
      });
    }, { threshold: 0.1 });
    observer.observe(video);
  })();

  /* ─────────────────────────────────────────────
     14. HERO STAT ANIMATED NUMBER
  ──────────────────────────────────────────────*/
  (function heroStat() {
    const el = document.getElementById('hero-stat-num');
    if (!el) return;
    const spans = el.querySelectorAll('span');
    if (!spans.length) return;

    spans.forEach((span, i) => {
      span.style.display = 'inline-block';
      span.style.opacity = '0';
      span.style.transform = 'translateY(12px)';
      span.style.transition = `opacity 0.4s ease ${2.8 + i * 0.06}s, transform 0.4s ease ${2.8 + i * 0.06}s`;
    });

    // Trigger after a brief delay
    setTimeout(() => {
      spans.forEach(span => {
        span.style.opacity = '1';
        span.style.transform = 'translateY(0)';
      });
    }, 200);
  })();

  /* ─────────────────────────────────────────────
     15. BENEFITS VIDEO AUTOPLAY
  ──────────────────────────────────────────────*/
  (function benefitsVideo() {
    const video = document.getElementById('benefits-video');
    if (!video) return;
    video.muted = true;
    video.defaultMuted = true;
    video.playsInline = true;

    const tryPlay = () => {
      if (video.paused) video.play().catch(() => {});
    };

    video.addEventListener('canplay', tryPlay);
    window.addEventListener('scroll', tryPlay, { passive: true, once: true });
  })();

  /* ─────────────────────────────────────────────
     16. FOOTER VIDEO
  ──────────────────────────────────────────────*/
  (function footerVideo() {
    const video = document.querySelector('.footer-video');
    if (!video) return;
    video.muted = true;
    video.defaultMuted = true;
    video.playsInline = true;

    const observer = new IntersectionObserver(entries => {
      entries.forEach(e => {
        if (e.isIntersecting && video.paused) video.play().catch(() => {});
      });
    }, { threshold: 0.05 });
    observer.observe(video);
  })();

  /* ─────────────────────────────────────────────
     17. HIGHLIGHT UNDERLINE CSS
  ──────────────────────────────────────────────*/
  (function highlightStyle() {
    const style = document.createElement('style');
    style.textContent = `
      .highlight-underline {
        position: relative;
        display: inline;
        color: inherit;
      }
      .highlight-underline::after {
        content: '';
        position: absolute;
        left: 0; right: 0;
        bottom: -3px;
        height: 3px;
        border-radius: 2px;
        background: #1d68f6;
      }
      /* Intro screen transitions */
      .intro-screen {
        position: fixed; inset: 0; z-index: 9999;
        background: #0f172a;
        display: flex; align-items: center; justify-content: center;
        transition: opacity 0.7s ease, transform 0.7s cubic-bezier(0.16,1,0.3,1);
      }
      .intro-screen.slide-up {
        opacity: 0; transform: translateY(-6%);
      }
      .intro-video { width: 100%; height: 100%; object-fit: cover; }

      /* Login modal */
      .login-fullscreen-overlay {
        position: fixed; inset: 0; z-index: 5000;
        background: rgba(0,0,0,0.6);
        backdrop-filter: blur(8px);
        display: none; align-items: center; justify-content: center;
        opacity: 0; transition: opacity 0.3s ease;
      }
      .login-fullscreen-overlay.is-open {
        display: flex; opacity: 1;
      }
      .login-content-wrapper {
        background: #fff; border-radius: 24px;
        box-shadow: 0 32px 80px rgba(0,0,0,0.25);
        display: flex; overflow: hidden;
        max-width: 900px; width: 90vw; max-height: 90vh;
        animation: loginSlideIn 0.4s cubic-bezier(0.16,1,0.3,1);
      }
      @keyframes loginSlideIn {
        from { opacity: 0; transform: scale(0.96) translateY(16px); }
        to { opacity: 1; transform: scale(1) translateY(0); }
      }
      .login-character-section {
        flex: 0 0 40%; background: linear-gradient(135deg,#eff6ff,#dbeafe);
        display: flex; align-items: center; justify-content: center; padding: 32px;
      }
      .login-char-container { width: 100%; max-width: 220px; }
      .login-char-img { width: 100%; height: auto; object-fit: contain; transition: src 0.3s ease; }
      .login-form-section { flex: 1; padding: 40px; overflow-y: auto; }
      .login-form-inner { max-width: 380px; }
      .login-brand-header { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
      .login-brand-mark { flex-shrink: 0; }
      .login-title { font-family: 'Lora','Georgia',serif; font-size: 24px; font-weight: 700; color: #0f1b2d; margin: 0; }
      .login-title em { color: #1d68f6; font-style: normal; }
      .login-subtitle { font-size: 13px; color: #64748b; margin: 2px 0 0; font-family: 'DM Mono',monospace; }
      .login-welcome-text { font-size: 15px; color: #334155; margin: 18px 0 20px; font-weight: 500; }
      .login-error-msg { display: flex; align-items: center; gap: 8px; background: #fef2f2; border: 1.5px solid #fecaca; border-radius: 10px; padding: 12px 16px; color: #dc2626; font-size: 14px; font-weight: 500; margin-bottom: 16px; }
      .login-form-fields { display: flex; flex-direction: column; gap: 16px; }
      .login-field-group { display: flex; flex-direction: column; gap: 6px; }
      .login-field-label { font-size: 13px; font-weight: 700; color: #374151; }
      .login-input-wrapper { position: relative; display: flex; align-items: center; }
      .login-input-icon { position: absolute; left: 14px; color: #94a3b8; pointer-events: none; display: flex; }
      .login-input { width: 100%; padding: 12px 14px 12px 42px; border: 2px solid #e2e8f0; border-radius: 10px; font-size: 15px; font-family: inherit; transition: border-color 0.2s ease, box-shadow 0.2s ease; color: #0f172a; outline: none; }
      .login-input:focus { border-color: #1d68f6; box-shadow: 0 0 0 3px rgba(29,104,246,0.15); }
      .login-pw-toggle { position: absolute; right: 12px; background: none; border: none; cursor: pointer; color: #94a3b8; padding: 4px; display: flex; }
      .login-options-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
      .login-remember { display: flex; align-items: center; gap: 6px; font-size: 14px; color: #374151; cursor: pointer; }
      .login-forgot-link { font-size: 14px; color: #1d68f6; text-decoration: none; font-weight: 600; white-space: nowrap; }
      .login-submit-btn { display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; padding: 14px; background: #1d68f6; color: #fff; border: none; border-radius: 10px; font-size: 16px; font-weight: 700; cursor: pointer; transition: background 0.2s ease, transform 0.15s ease; }
      .login-submit-btn:hover { background: #1b47cb; transform: translateY(-1px); }
      .login-submit-btn:disabled { opacity: 0.7; cursor: not-allowed; }
      .login-btn-spinner { animation: spin 0.8s linear infinite; }
      @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      .login-note { display: flex; align-items: flex-start; gap: 8px; margin-top: 16px; padding: 12px 14px; background: #f8fafc; border-radius: 8px; color: #64748b; font-size: 13px; line-height: 1.5; }
      .login-close-btn { position: absolute; top: 20px; right: 20px; z-index: 10; width: 36px; height: 36px; border-radius: 50%; background: #fff; border: none; cursor: pointer; display: flex; align-items: center; justify-content: center; box-shadow: 0 4px 12px rgba(0,0,0,0.15); color: #374151; transition: transform 0.2s ease; }
      .login-close-btn:hover { transform: scale(1.1); }

      /* Legal modal */
      .legal-modal-overlay { position: fixed; inset: 0; z-index: 4000; background: rgba(0,0,0,0.5); backdrop-filter: blur(6px); display: none; align-items: center; justify-content: center; }
      .legal-modal-overlay.is-open { display: flex; }
      .legal-modal-container { background: #fff; border-radius: 20px; box-shadow: 0 32px 80px rgba(0,0,0,0.2); width: 90vw; max-width: 720px; max-height: 85vh; display: flex; flex-direction: column; animation: loginSlideIn 0.4s cubic-bezier(0.16,1,0.3,1); position: relative; }
      .legal-close-btn { position: absolute; top: 16px; right: 16px; width: 32px; height: 32px; border-radius: 50%; background: #f1f5f9; border: none; cursor: pointer; display: flex; align-items: center; justify-content: center; color: #374151; transition: background 0.2s; }
      .legal-close-btn:hover { background: #e2e8f0; }
      .legal-tabs { display: flex; gap: 0; border-bottom: 2px solid #e2e8f0; padding: 0 24px; }
      .legal-tab { padding: 16px 20px; font-size: 15px; font-weight: 600; color: #64748b; border: none; background: none; cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: color 0.2s, border-color 0.2s; }
      .legal-tab.active { color: #1d68f6; border-bottom-color: #1d68f6; }
      .legal-modal-body { overflow-y: auto; padding: 28px 28px; flex: 1; }
      .legal-tab-content { display: none; }
      .legal-tab-content.active { display: block; }
      .legal-title { font-family: 'Playfair Display',serif; font-size: 26px; color: #0f172a; margin: 0 0 4px; font-weight: 700; }
      .legal-date { font-size: 13px; color: #94a3b8; font-family: 'DM Mono',monospace; margin: 0 0 24px; }
      .legal-section { margin-bottom: 20px; }
      .legal-section h3 { font-size: 16px; font-weight: 700; color: #0f172a; margin: 0 0 8px; }
      .legal-section p { font-size: 15px; color: #374151; line-height: 1.7; margin: 0; }
      .legal-section a { color: #1d68f6; }

      /* Overlay menu */
      .overlay-menu-container { position: fixed; inset: 0; z-index: 900; pointer-events: none; }
      .overlay-menu-container.is-open { pointer-events: auto; }
      .overlay-menu-bg { position: absolute; inset: 0; background: rgba(10,18,32,0.92); backdrop-filter: blur(12px); opacity: 0; transition: opacity 0.4s ease; }
      .overlay-menu-container.is-open .overlay-menu-bg { opacity: 1; }
      .overlay-menu-content { position: absolute; inset: 0; display: flex; flex-direction: column; justify-content: center; padding: 0 clamp(24px,8vw,120px); gap: 4px; }
      .overlay-menu-item { display: flex; flex-direction: column; gap: 2px; padding: 12px 0; text-decoration: none; opacity: 0; transform: translateX(-24px); transition: opacity 0.4s ease, transform 0.4s cubic-bezier(0.16,1,0.3,1); }
      .overlay-menu-container.is-open .overlay-menu-item { opacity: 1; transform: none; }
      .overlay-menu-chapter { font-size: 11px; font-weight: 700; color: rgba(255,255,255,0.4); letter-spacing: 0.14em; font-family: 'DM Mono',monospace; }
      .overlay-menu-label { font-size: clamp(32px,6vw,72px); font-weight: 700; color: #fff; letter-spacing: -0.03em; font-family: 'Playfair Display',Georgia,serif; line-height: 1; transition: color 0.2s; }
      .overlay-menu-item:hover .overlay-menu-label, .overlay-menu-item.is-active .overlay-menu-label { color: #60a5fa; }
      .overlay-menu-login-wrap { margin-top: 24px; opacity: 0; transition: opacity 0.4s ease 0.4s; }
      .overlay-menu-container.is-open .overlay-menu-login-wrap { opacity: 1; }
      .overlay-menu-login-btn { display: inline-flex; align-items: center; gap: 10px; padding: 12px 20px; border-radius: 12px; background: rgba(255,255,255,0.1); border: 1.5px solid rgba(255,255,255,0.2); color: #fff; font-size: 15px; font-weight: 600; cursor: pointer; transition: background 0.2s; }
      .overlay-menu-login-btn:hover { background: rgba(255,255,255,0.18); }

      /* Nav dropdown */
      .nav-dropdown-menu { position: absolute; top: 100%; left: -20px; min-width: 280px; background: #fff; border-radius: 16px; box-shadow: 0 20px 48px rgba(15,27,45,0.14), 0 4px 16px rgba(15,27,45,0.06); border: 1.5px solid #e2e8f0; padding: 8px; opacity: 0; pointer-events: none; transform: translateY(8px); transition: opacity 0.2s ease, transform 0.2s ease; }
      .nav-dropdown-menu.is-open { opacity: 1; pointer-events: auto; transform: translateY(0); }
      .nav-dropdown-link { display: flex; flex-direction: column; gap: 2px; padding: 12px 14px; border-radius: 10px; text-decoration: none; transition: background 0.15s; }
      .nav-dropdown-link:hover { background: #f8fafc; }
      .nav-dropdown-link .d-title { font-size: 14px; font-weight: 700; color: #0f1b2d; }
      .nav-dropdown-link .d-desc { font-size: 13px; color: #64748b; line-height: 1.4; }

      /* Right nav group */
      .nav-right-group { display: flex; align-items: center; gap: 12px; }
      .nav-login-btn { padding: 8px 16px; background: none; border: 1.5px solid #e2e8f0; border-radius: 8px; font-size: 14px; font-weight: 600; color: #0f1b2d; cursor: pointer; transition: all 0.2s; }
      .nav-login-btn:hover { border-color: #1d68f6; color: #1d68f6; }
      .navarino-blue-btn { display: inline-flex; align-items: center; padding: 10px 18px; background: #1d68f6; color: #fff; border-radius: 8px; font-size: 14px; font-weight: 700; text-decoration: none; transition: background 0.2s; white-space: nowrap; }
      .navarino-blue-btn:hover { background: #1b47cb; }
      .navarino-mobile-toggle { display: none; width: 40px; height: 40px; border-radius: 8px; background: none; border: 1.5px solid #e2e8f0; align-items: center; justify-content: center; cursor: pointer; color: #0f1b2d; }
      @media (max-width: 1024px) { .navarino-links-desktop { display: none; } .navarino-mobile-toggle { display: flex; } }
      @media (max-width: 768px) { .nav-login-btn, .navarino-blue-btn { display: none; } }
    `;
    document.head.appendChild(style);
  })();

  /* ─────────────────────────────────────────────
     18. RE-OBSERVE NEW ELEMENTS (for HTMX swaps)
  ──────────────────────────────────────────────*/
  document.body.addEventListener('htmx:afterSwap', () => {
    const observer = new IntersectionObserver(
      entries => entries.forEach(e => {
        if (e.isIntersecting) { e.target.classList.add('in-view'); observer.unobserve(e.target); }
      }),
      { threshold: 0.1 }
    );
    document.querySelectorAll('.animate-on-scroll:not(.in-view)').forEach(el => observer.observe(el));
  });

})();
