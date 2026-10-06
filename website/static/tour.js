(() => {
  const tour = document.querySelector('[data-tour]');
  if (!tour) return;

  const slides = [...tour.querySelectorAll('[data-slide]')];
  const picker = tour.querySelector('[data-tour-picker]');
  const previous = tour.querySelector('[data-tour-prev]');
  const next = tour.querySelector('[data-tour-next]');
  const position = tour.querySelector('[data-tour-position]');
  const slug = slide => slide.id.replace(/^slide-/, '');
  const indexFromHash = () => slides.findIndex(slide => `#tour-${slug(slide)}` === location.hash);
  let current = 0;

  function show(index, { history = false } = {}) {
    current = Math.max(0, Math.min(slides.length - 1, index));
    slides.forEach((slide, i) => {
      slide.hidden = i !== current;
      slide.setAttribute('aria-hidden', String(i !== current));
    });
    picker.value = String(current + 1);
    position.textContent = `${current + 1} of ${slides.length}`;
    previous.disabled = current === 0;
    next.disabled = current === slides.length - 1;
    if (history) historyPush();
  }

  function historyPush() {
    const hash = `#tour-${slug(slides[current])}`;
    if (location.hash !== hash) window.history.pushState({ tourSlide: current }, '', `${location.pathname}${location.search}${hash}`);
  }

  tour.dataset.enhanced = 'true';
  const initial = indexFromHash();
  if (location.hash.startsWith('#tour-') && initial < 0) {
    window.history.replaceState(null, '', `${location.pathname}${location.search}`);
  }
  show(initial < 0 ? 0 : initial);

  previous.addEventListener('click', () => show(current - 1, { history: true }));
  next.addEventListener('click', () => show(current + 1, { history: true }));
  picker.addEventListener('change', () => show(Number(picker.value) - 1, { history: true }));
  function followHash() {
    const index = indexFromHash();
    if (index >= 0) return show(index);
    if (!location.hash || location.hash.startsWith('#tour-')) {
      show(0);
      if (location.hash) window.history.replaceState(null, '', `${location.pathname}${location.search}`);
    }
  }
  window.addEventListener('popstate', followHash);
  window.addEventListener('hashchange', () => {
    followHash();
  });
  tour.addEventListener('keydown', event => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.target.matches('input, textarea, select, [contenteditable="true"]')) return;
    if (event.key === 'ArrowLeft' && current > 0) {
      event.preventDefault();
      show(current - 1, { history: true });
    } else if (event.key === 'ArrowRight' && current < slides.length - 1) {
      event.preventDefault();
      show(current + 1, { history: true });
    }
  });
})();
