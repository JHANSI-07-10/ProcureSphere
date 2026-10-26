/**
 * ProcureSphere 360 — Core Capabilities Animated Deck
 * Reproduces AnimatedModulesStack.tsx behaviour in vanilla JS:
 *   - Three auto-cycling states: fanned → messy → stacked → fanned
 *   - Spring-physics card transforms (approximated with CSS transitions)
 *   - Hover: card lifts and highlights
 *   - Click: module detail modal
 *   - Mobile: swipeable carousel with prev/next buttons and dot indicators
 *   - "Explore Details" navigates to /solutions/?step=<stepId>
 */
(function () {
  'use strict';

  /* ── helpers ── */
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function lerp(a, b, t) { return a + (b - a) * t; }

  /* ── module data (mirrors views.py cap_modules order exactly) ── */
  const cards = Array.from(document.querySelectorAll('.cap-card'));
  if (!cards.length) return;            // section not on this page

  const N = cards.length;               // 6

  /* ── state ── */
  let animState = 'fanned';             // 'stacked' | 'fanned' | 'messy'
  let hoveredIdx = null;
  let isHovered = false;
  let selectedCard = null;
  let cycleTimer = null;

  /* ── responsive breakpoints ── */
  function ww() { return window.innerWidth; }
  const isMobile  = () => ww() < 600;
  const isTablet  = () => ww() >= 600 && ww() <= 1280;

  /* ── spring approximation via CSS transition + JS target ── */
  // We keep per-card "current" and "target" transforms and step them each
  // rAF frame using a damped spring coefficient.
  const state = cards.map((_, i) => ({
    x: 0, y: 0, rz: 0, scale: 1,         // current
    tx: 0, ty: 0, trz: 0, tscale: 1,     // target
  }));

  /* ── get target transform for a given animation state ── */
  function getTarget(index, aState) {
    const centerOffset = index - (N / 2 - 0.5);  // −2.5 … 2.5

    if (aState === 'fanned') {
      let spacing = 160, scale = 1, yMul = 20, rzMul = 8;
      if (isMobile()) {
        spacing = 36; scale = 0.92; yMul = 8; rzMul = 5;
      } else if (isTablet()) {
        const p = clamp((ww() - 600) / 680, 0, 1);
        scale   = lerp(0.68, 0.94, p);
        spacing = lerp(68,   146,  p);
        yMul    = lerp(12,   20,   p);
        rzMul   = lerp(5.5,  8,    p);
      }
      return {
        tx: centerOffset * spacing,
        ty: Math.abs(centerOffset) * yMul,
        trz: centerOffset * rzMul,
        tscale: scale,
      };
    }

    if (aState === 'messy') {
      const jitterRz = isMobile()  ? [-8,6,-4,8,-6,10]   : [-14,8,-6,12,-10,16];
      const jitterX  = isMobile()  ? [-18,14,-8,16,-12,18]
                      : isTablet() ? [-28,18,-12,22,-16,26]
                                   : [-40,25,-15,30,-20,35];
      const jitterY  = isMobile()  ? [6,-8,3,-4,6,-2]    : [10,-15,5,-8,12,-4];
      const sc = isMobile() ? 0.9 : isTablet() ? 0.82 : 0.98;
      return {
        tx: jitterX[index] || 0,
        ty: jitterY[index] || 0,
        trz: jitterRz[index] || 0,
        tscale: sc,
      };
    }

    // stacked
    const stackedRz = [-5,3,-2,4,-3,2];
    return {
      tx: 0,
      ty: index * -4,
      trz: stackedRz[index] || 0,
      tscale: isMobile() ? 0.92 : isTablet() ? 0.85 : 0.95,
    };
  }

  /* ── apply CSS transform to a card ── */
  function applyTransform(el, x, y, rz, sc, zIndex, transition) {
    el.style.transition = transition || 'transform 0.45s cubic-bezier(0.16,1,0.3,1)';
    el.style.transform  = `translate(${x}px,${y}px) rotate(${rz}deg) scale(${sc})`;
    if (zIndex !== undefined) el.style.zIndex = zIndex;
  }

  /* ── refresh all cards to current animState targets ── */
  function refreshDeck(state) {
    cards.forEach((card, i) => {
      const t = getTarget(i, state);
      const zIdx = hoveredIdx === i ? 50 : i;
      if (hoveredIdx === i) {
        // Hover: lift card up, increase scale slightly
        const ht = getTarget(i, animState);
        const liftY = ht.ty - 14;
        const liftSc = isTablet() ? ht.tscale * 1.06 : 1.1;
        applyTransform(card, ht.tx, liftY, ht.trz, liftSc, 50,
          'transform 0.22s cubic-bezier(0.16,1,0.3,1), box-shadow 0.2s ease');
        card.style.boxShadow = '0 24px 64px rgba(0,0,0,0.4)';
        card.style.border = '2px solid #ffffff';
      } else {
        applyTransform(card, t.tx, t.ty, t.trz, t.tscale, zIdx,
          'transform 0.48s cubic-bezier(0.16,1,0.3,1)');
        card.style.boxShadow = '0 12px 32px rgba(15,27,45,0.18)';
        card.style.border = `2px solid ${card.dataset.modColor}`;
      }
    });
  }

  /* ── auto-cycle timer ── */
  const TIMINGS = { stacked: 400, fanned: 3000, messy: 600 };
  const NEXT    = { stacked: 'fanned', fanned: 'messy', messy: 'stacked' };

  function scheduleCycle() {
    clearTimeout(cycleTimer);
    if (selectedCard || isHovered) return;
    cycleTimer = setTimeout(() => {
      animState = NEXT[animState];
      refreshDeck(animState);
      scheduleCycle();
    }, TIMINGS[animState]);
  }

  /* ── hover events ── */
  const deckEl = document.getElementById('cap-deck-desktop');
  if (deckEl) {
    deckEl.addEventListener('mouseenter', () => {
      isHovered = true;
      clearTimeout(cycleTimer);
    });
    deckEl.addEventListener('mouseleave', () => {
      isHovered = false;
      hoveredIdx = null;
      refreshDeck(animState);
      scheduleCycle();
    });
  }

  cards.forEach((card, i) => {
    card.addEventListener('mouseenter', () => {
      hoveredIdx = i;
      refreshDeck(animState);
    });
    card.addEventListener('mouseleave', () => {
      hoveredIdx = null;
      refreshDeck(animState);
    });
    card.addEventListener('click',   () => openModal(card));
    card.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') openModal(card); });
  });

  /* ── MODULE MODAL ── */
  const modal     = document.getElementById('cap-module-modal');
  const modalInner = document.getElementById('cap-modal-inner');
  const modalClose = document.getElementById('cap-modal-close');
  const modalIcon  = document.getElementById('cap-modal-icon');
  const modalPath  = document.getElementById('cap-modal-path');
  const modalTitle = document.getElementById('cap-modal-title');
  const modalDesc  = document.getElementById('cap-modal-desc');
  const modalCta   = document.getElementById('cap-modal-cta');
  const modalCtaLabel = document.getElementById('cap-modal-cta-label');

  function openModal(card) {
    if (!modal) return;
    selectedCard = card;
    clearTimeout(cycleTimer);

    const color   = card.dataset.modColor;
    const title   = card.dataset.modTitle;
    const longDesc = card.dataset.modLong;
    const stepId  = card.dataset.modStep;
    const icon    = card.dataset.modIcon;

    // Populate modal content
    modalIcon.style.background = color + '15';
    modalIcon.style.color      = color;
    modalPath.setAttribute('d', icon);
    modalTitle.textContent = title;
    modalDesc.textContent  = longDesc;
    modalCta.href          = `/solutions/?step=${stepId}#lifecycle-detail`;
    modalCta.style.background = color;
    modalCta.style.boxShadow  = `0 8px 24px ${color}40`;
    if (modalCtaLabel) modalCtaLabel.textContent = `Explore ${title}`;

    // Show modal
    modal.style.display = 'flex';
    // Trigger animation re-run (force reflow)
    void modalInner.offsetWidth;
    modalInner.style.animation = 'none';
    void modalInner.offsetWidth;
    modalInner.style.animation = 'capModalIn 0.35s cubic-bezier(0.16,1,0.3,1)';
    document.body.style.overflow = 'hidden';
  }

  function closeModal() {
    if (!modal) return;
    modal.style.display = 'none';
    document.body.style.overflow = '';
    selectedCard = null;
    scheduleCycle();
  }

  if (modalClose) modalClose.addEventListener('click', closeModal);
  if (modal) {
    modal.addEventListener('click', e => {
      if (e.target === modal) closeModal();
    });
  }
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && modal && modal.style.display !== 'none') closeModal();
  });

  /* ── MOBILE CAROUSEL ── */
  const mobileWrap = document.getElementById('cap-mobile-card-wrap');
  const dotsEl     = document.getElementById('cap-mobile-dots');
  const counter    = document.getElementById('cap-mobile-counter');
  const prevBtn    = document.getElementById('cap-prev-btn');
  const nextBtn    = document.getElementById('cap-next-btn');

  // Collect raw module data from card DOM attributes
  const moduleData = cards.map(c => ({
    color:   c.dataset.modColor,
    title:   c.dataset.modTitle,
    desc:    c.dataset.modDesc,
    longDesc: c.dataset.modLong,
    stepId:  c.dataset.modStep,
    icon:    c.dataset.modIcon,
  }));

  let mobileIdx = 0;
  let slideDir  = 1;
  let isTouching = false;
  let touchStartX = 0;

  function buildMobileCard(mod) {
    const el = document.createElement('div');
    el.style.cssText = `
      width:100%;min-height:320px;border-radius:24px;
      background:${mod.color};
      box-shadow:0 14px 34px ${mod.color}45, 0 4px 12px rgba(0,0,0,0.08);
      border:2px solid rgba(255,255,255,0.25);
      padding:30px 22px;display:flex;flex-direction:column;
      justify-content:center;align-items:center;text-align:center;
      cursor:pointer;touch-action:pan-y;box-sizing:border-box;
    `;
    el.innerHTML = `
      <div style="width:56px;height:56px;border-radius:16px;background:rgba(255,255,255,0.22);color:#fff;display:flex;align-items:center;justify-content:center;margin-bottom:18px;flex-shrink:0;">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="${mod.icon}"/></svg>
      </div>
      <h3 style="font-size:20px;font-weight:700;color:#fff;margin:0 0 10px;letter-spacing:-0.02em;">${mod.title}</h3>
      <p style="font-size:14px;color:rgba(255,255,255,0.92);line-height:1.5;margin:0 0 18px;">${mod.desc}</p>
      <div style="font-size:12px;font-weight:700;color:#fff;display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,0.25);padding:7px 14px;border-radius:999px;border:1px solid rgba(255,255,255,0.3);">
        <span>Explore Details</span><span aria-hidden="true">→</span>
      </div>
    `;
    el.addEventListener('click', () => {
      // Open modal with a synthetic card-like object
      const fakeCard = {
        dataset: {
          modColor: mod.color,
          modTitle: mod.title,
          modLong:  mod.longDesc,
          modStep:  mod.stepId,
          modIcon:  mod.icon,
        }
      };
      openModal(fakeCard);
    });
    return el;
  }

  function buildDots() {
    if (!dotsEl) return;
    dotsEl.innerHTML = '';
    moduleData.forEach((m, i) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.setAttribute('aria-label', `Go to ${m.title}`);
      btn.style.cssText = `
        width:${i === mobileIdx ? '22px' : '7px'};height:7px;border-radius:999px;
        background:${i === mobileIdx ? m.color : '#cbd5e1'};
        border:none;cursor:pointer;padding:0;
        transition:all 0.3s cubic-bezier(0.4,0,0.2,1);
      `;
      btn.addEventListener('click', () => {
        slideDir = i > mobileIdx ? 1 : -1;
        mobileIdx = i;
        renderMobileCard(false);
      });
      dotsEl.appendChild(btn);
    });
  }

  function renderMobileCard(animate = true) {
    if (!mobileWrap) return;
    const mod = moduleData[mobileIdx];
    const newCard = buildMobileCard(mod);

    if (animate) {
      const fromX = slideDir > 0 ? 40 : -40;
      newCard.style.opacity   = '0';
      newCard.style.transform = `translateX(${fromX}px) scale(0.94)`;
      newCard.style.transition = 'none';
    }

    mobileWrap.innerHTML = '';
    mobileWrap.appendChild(newCard);

    if (animate) {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          newCard.style.transition = 'opacity 0.32s ease, transform 0.38s cubic-bezier(0.16,1,0.3,1)';
          newCard.style.opacity    = '1';
          newCard.style.transform  = 'translateX(0) scale(1)';
        });
      });
    }

    // Update counter
    if (counter) counter.textContent = `${mobileIdx + 1} of ${moduleData.length}`;

    // Rebuild dots
    buildDots();
  }

  if (prevBtn) {
    prevBtn.addEventListener('click', e => {
      e.stopPropagation();
      slideDir  = -1;
      mobileIdx = (mobileIdx - 1 + N) % N;
      renderMobileCard(true);
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener('click', e => {
      e.stopPropagation();
      slideDir  = 1;
      mobileIdx = (mobileIdx + 1) % N;
      renderMobileCard(true);
    });
  }

  // Touch swipe on mobile card wrapper
  if (mobileWrap) {
    mobileWrap.addEventListener('touchstart', e => {
      touchStartX  = e.touches[0].clientX;
      isTouching   = true;
    }, { passive: true });
    mobileWrap.addEventListener('touchend', e => {
      if (!isTouching) return;
      isTouching = false;
      const dx = e.changedTouches[0].clientX - touchStartX;
      if (dx < -30) {
        slideDir  = 1;
        mobileIdx = (mobileIdx + 1) % N;
        renderMobileCard(true);
      } else if (dx > 30) {
        slideDir  = -1;
        mobileIdx = (mobileIdx - 1 + N) % N;
        renderMobileCard(true);
      }
    }, { passive: true });
  }

  /* ── RESPONSIVE: swap desktop/mobile layout ── */
  function applyLayout() {
    const desktopEl = document.getElementById('cap-deck-desktop');
    const mobileEl  = document.getElementById('cap-deck-mobile');
    if (!desktopEl || !mobileEl) return;

    if (isMobile()) {
      desktopEl.style.display = 'none';
      mobileEl.style.display  = 'flex';
      if (!mobileWrap.hasChildNodes()) renderMobileCard(false);
    } else {
      desktopEl.style.display = 'flex';
      mobileEl.style.display  = 'none';
    }
    // Also refresh deck for new breakpoint transforms
    refreshDeck(animState);
  }

  /* ── IntersectionObserver: start animation when section enters viewport ── */
  const section = document.getElementById('modules');
  let hasStarted = false;
  if (section) {
    const io = new IntersectionObserver(entries => {
      entries.forEach(e => {
        if (e.isIntersecting && !hasStarted) {
          hasStarted = true;
          refreshDeck(animState);
          scheduleCycle();
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.15 });
    io.observe(section);
  } else {
    // Fallback: start immediately
    refreshDeck(animState);
    scheduleCycle();
  }

  /* ── resize handler ── */
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      applyLayout();
      refreshDeck(animState);
    }, 120);
  }, { passive: true });

  /* ── initial layout ── */
  applyLayout();

})();
