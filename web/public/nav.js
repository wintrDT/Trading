document.addEventListener('DOMContentLoaded', () => {
  const dropdowns = [];

  document.querySelectorAll('.nav-dropdown').forEach(dd => {
    const trigger = dd.querySelector('.nav-dropdown-trigger');
    const menu    = dd.querySelector('.nav-dropdown-menu');
    if (!trigger || !menu) return;

    const entry = { trigger, menu, originalParent: menu.parentNode, isOpen: false };
    dropdowns.push(entry);

    trigger.addEventListener('click', e => {
      e.stopPropagation();
      const opening = !entry.isOpen;
      closeAll();
      if (opening) openDropdown(entry);
    });
  });

  function openDropdown(entry) {
    const { trigger, menu } = entry;
    if (window.innerWidth <= 768) {
      // Move to <body> so the menu is in the root stacking context,
      // completely outside the navbar stacking context (sticky + z-index).
      const rect = trigger.getBoundingClientRect();
      document.body.appendChild(menu);
      menu.style.cssText =
        `position:fixed;top:${rect.bottom + 4}px;left:${rect.left}px;` +
        `z-index:99999;min-width:160px;`;
    } else {
      menu.style.cssText = '';
    }
    menu.classList.add('open');
    trigger.classList.add('open');
    entry.isOpen = true;
  }

  function closeAll() {
    dropdowns.forEach(({ trigger, menu, originalParent }) => {
      menu.classList.remove('open');
      trigger.classList.remove('open');
      if (menu.parentNode !== originalParent) {
        originalParent.appendChild(menu);
      }
      menu.style.cssText = '';
    });
    dropdowns.forEach(e => { e.isOpen = false; });
  }

  document.addEventListener('click', closeAll);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeAll(); });
});
