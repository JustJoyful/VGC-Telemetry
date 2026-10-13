'use strict';
(() => {
  const root = document.documentElement;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const system = matchMedia('(prefers-color-scheme: light)');
  const themes = {light:'dex-day', dark:'dex-night', 'dex-day':'dex-day', 'dex-night':'dex-night'};
  let preference, motion;
  try { preference = themes[localStorage.getItem('vgc-theme')]; motion = localStorage.getItem('vgc-motion'); } catch {}
  root.classList.add('js');
  root.dataset.theme = preference || (system.matches ? 'dex-day' : 'dex-night');
  root.dataset.motion = reduced.matches || motion === 'paused' ? 'paused' : 'on';
  function controls() {
    document.querySelectorAll('button[data-theme-toggle]').forEach(button => {
      const day = root.dataset.theme === 'dex-day';
      button.setAttribute('aria-label', day ? 'Switch to dark mode' : 'Switch to light mode');
      button.setAttribute('aria-pressed', String(day));
      button.title = button.getAttribute('aria-label');
    });
    document.querySelectorAll('button[data-motion-toggle]').forEach(button => {
      const paused = root.dataset.motion === 'paused';
      button.setAttribute('aria-label', reduced.matches ? 'Motion reduced' : paused ? 'Resume motion' : 'Pause motion');
      button.setAttribute('aria-pressed', String(paused));
      button.disabled = reduced.matches;
      button.title = button.getAttribute('aria-label');
    });
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('button[data-theme-toggle],button[data-motion-toggle]');
    if (!button) return;
    if (button.hasAttribute('data-theme-toggle')) {
      preference = root.dataset.theme === 'dex-day' ? 'dex-night' : 'dex-day';
      root.dataset.theme = preference;
      try { localStorage.setItem('vgc-theme', preference); } catch {}
    } else if (!reduced.matches) {
      motion = root.dataset.motion === 'paused' ? 'on' : 'paused';
      root.dataset.motion = motion;
      try { localStorage.setItem('vgc-motion', motion); } catch {}
    }
    controls();
  });
  system.addEventListener('change', () => { if (!preference) root.dataset.theme = system.matches ? 'dex-day' : 'dex-night'; controls(); });
  reduced.addEventListener('change', () => { root.dataset.motion = reduced.matches || motion === 'paused' ? 'paused' : 'on'; controls(); });
  function sprites() {
    document.querySelectorAll('.sprite-slot img').forEach(img => {
      if (img.dataset.checked) return;
      img.dataset.checked = 'true';
      const fail = () => { img.closest('.sprite-slot').dataset.unavailable = 'true'; };
      img.addEventListener('error', fail);
      if (img.complete && !img.naturalWidth) fail();
    });
  }
  document.addEventListener('DOMContentLoaded', () => { controls(); sprites(); }, {once:true});
  function status(text) {
    const node = document.getElementById('request-status');
    if (node) node.textContent = text;
  }
  document.addEventListener('htmx:beforeRequest', event => {
    const path = new URL(event.detail.requestConfig.path, location.href).pathname;
    if (event.detail.boosted && (!path.startsWith('/explorer') || path.startsWith('/explorer/static/'))) {
      event.preventDefault();
      location.assign(event.detail.requestConfig.path);
      return;
    }
    status('Reading query results…');
  });
  document.addEventListener('htmx:beforeSwap', event => {
    if (event.detail.xhr.status >= 400) { event.detail.shouldSwap = true; event.detail.isError = false; }
  });
  document.addEventListener('htmx:afterSwap', event => {
    controls(); sprites(); status('Query results updated.');
    if (['main','results'].includes(event.detail.target.id)) document.getElementById(event.detail.target.id)?.focus({preventScroll:true});
  });
  document.addEventListener('htmx:sendError', () => status('Query unavailable. Try again or use the normal page link.'));
})();
