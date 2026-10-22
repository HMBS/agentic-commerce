/* Fitzwilliam Provisions — storefront behaviour
   Cart state lives in localStorage. Prices come from the DOM (data-price, minor units),
   which the verification script cross-checks against products.json. */

(() => {
  'use strict';

  const $  = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  const eur = new Intl.NumberFormat('en-IE', { style: 'currency', currency: 'EUR' });
  const money = (minor) => eur.format(minor / 100);
  const STORE_CART = 'fv.cart.v1';
  const STORE_AGE  = 'fv.age.v1';

  /* ── Product model, read from the rendered cards ─────────────────────── */
  const products = new Map();
  $$('[data-product-id]').forEach((card) => {
    products.set(card.dataset.productId, {
      id: card.dataset.productId,
      name: $('h3', card).textContent.trim(),
      price: Number(card.dataset.price),
      restriction: card.dataset.restriction || null,
      image: $('img', card)?.getAttribute('src') || '',
      card
    });
  });

  /* ── State ───────────────────────────────────────────────────────────── */
  const readJSON = (key, fallback) => {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; }
    catch { return fallback; }
  };
  let cart = readJSON(STORE_CART, {});           // { productId: qty }
  let ageVerified = localStorage.getItem(STORE_AGE) === '1';
  let pendingAdd = null;

  const persist = () => localStorage.setItem(STORE_CART, JSON.stringify(cart));

  const cartLines = () => Object.entries(cart)
    .filter(([id, qty]) => products.has(id) && qty > 0)
    .map(([id, qty]) => ({ ...products.get(id), qty }));

  const cartCount = () => cartLines().reduce((n, l) => n + l.qty, 0);
  const cartTotal = () => cartLines().reduce((n, l) => n + l.price * l.qty, 0);
  const hasRestricted = () => cartLines().some((l) => l.restriction);
  const restrictedTotal = () => cartLines()
    .filter((l) => l.restriction)
    .reduce((n, l) => n + l.price * l.qty, 0);

  /* ── Toast ───────────────────────────────────────────────────────────── */
  const toastEl = $('[data-toast]');
  let toastTimer;
  const toast = (msg) => {
    toastEl.textContent = msg;
    toastEl.classList.add('is-open');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove('is-open'), 2600);
  };

  /* ── Cart rendering ──────────────────────────────────────────────────── */
  const itemsEl = $('[data-cart-items]');

  function renderCart() {
    const lines = cartLines();
    $('[data-cart-count]').textContent = String(cartCount());

    if (!lines.length) {
      itemsEl.innerHTML = '<p class="empty">Nothing in the basket yet.<br>The coffee is a good place to start.</p>';
    } else {
      itemsEl.innerHTML = lines.map((l) => `
        <div class="line-item" data-line="${l.id}">
          <div class="line-item__media"><img src="${l.image}" alt="" width="64" height="64"></div>
          <div>
            <h3>${l.name}</h3>
            <p>${l.restriction ? '18+ · age check required' : 'In stock'} · ${money(l.price)}</p>
            <div class="qty" style="margin-top:8px">
              <button type="button" data-qty="-1" data-id="${l.id}" aria-label="Remove one ${l.name}">−</button>
              <span>${l.qty}</span>
              <button type="button" data-qty="1" data-id="${l.id}" aria-label="Add one ${l.name}">+</button>
            </div>
          </div>
          <span class="price">${money(l.price * l.qty)}</span>
        </div>`).join('');
    }

    $('[data-cart-total]').textContent = money(cartTotal());
    $('[data-cart-meta]').textContent = `${cartCount()} item${cartCount() === 1 ? '' : 's'} · VAT included`;
    $('[data-gate-warning]').hidden = !(hasRestricted() && !ageVerified);
    $('[data-checkout]').disabled = lines.length === 0;
    renderAgentPayload();
  }

  const payload = () => ({
    agent: {
      id: 'hermes-desktop-test',
      profile: 'https://example.test/agents/hermes-desktop.json',
      mandate: null
    },
    currency: 'EUR',
    age_verified: ageVerified,
    items: cartLines().map((l) => ({ id: l.id, quantity: l.qty }))
  });

  function renderAgentPayload() {
    const el = $('[data-agent-json]');
    if (el) el.textContent = JSON.stringify(payload(), null, 2);
  }

  /* ── Cart actions ────────────────────────────────────────────────────── */
  function addToCart(id, qty = 1) {
    const p = products.get(id);
    if (!p) return;
    cart[id] = Math.min(99, (cart[id] || 0) + qty);
    persist();
    renderCart();
    toast(`${p.name} added`);
  }

  $$('[data-add]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.add;
      const p = products.get(id);
      if (p?.restriction && !ageVerified) {
        pendingAdd = id;
        openAgeGate();
        return;
      }
      addToCart(id);
    });
  });

  itemsEl.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-qty]');
    if (!btn) return;
    const id = btn.dataset.id;
    const next = (cart[id] || 0) + Number(btn.dataset.qty);
    if (next <= 0) delete cart[id]; else cart[id] = next;
    persist();
    renderCart();
  });

  /* ── Drawer ──────────────────────────────────────────────────────────── */
  const drawer = $('[data-drawer]');
  const scrim = $('[data-scrim]');
  let lastFocus = null;

  function openCart() {
    lastFocus = document.activeElement;
    drawer.classList.add('is-open');
    drawer.setAttribute('aria-hidden', 'false');
    scrim.classList.add('is-open');
    document.body.style.overflow = 'hidden';
    setTimeout(() => $('[data-cart-close]').focus(), 60);
  }
  function closeCart() {
    drawer.classList.remove('is-open');
    drawer.setAttribute('aria-hidden', 'true');
    scrim.classList.remove('is-open');
    document.body.style.overflow = '';
    lastFocus?.focus?.();
  }
  $$('[data-cart-open]').forEach((b) => b.addEventListener('click', openCart));
  $('[data-cart-close]').addEventListener('click', closeCart);
  scrim.addEventListener('click', closeCart);

  /* Tabs */
  const tabs = $$('[data-tab]');
  function selectTab(name) {
    tabs.forEach((t) => t.setAttribute('aria-selected', String(t.dataset.tab === name)));
    $$('[data-panel]').forEach((p) => { p.hidden = p.dataset.panel !== name; });
  }
  tabs.forEach((t) => t.addEventListener('click', () => selectTab(t.dataset.tab)));

  /* ── Age gate ────────────────────────────────────────────────────────── */
  const ageModal = $('[data-age-modal]');
  const agePanel = $('[data-age-panel]');
  const ageBtn = $('[data-age-open]');

  function paintAgeState() {
    $('[data-age-state]').textContent = ageVerified ? 'Verified · 18+' : 'Not verified';
    agePanel?.classList.toggle('is-verified', ageVerified);
    ageBtn.textContent = ageVerified ? 'Verified' : 'Verify age';
    $$('.js-age-add').forEach((btn) => {
      btn.classList.toggle('btn--consent', !ageVerified);
      btn.classList.toggle('btn--ink', ageVerified);
      btn.textContent = ageVerified ? 'Add' : '18+ · Confirm age';
    });
  }

  function openAgeGate() {
    lastFocus = document.activeElement;
    ageModal.classList.add('is-open');
    document.body.style.overflow = 'hidden';
    setTimeout(() => $('[data-age-confirm]').focus(), 60);
  }
  function closeAgeGate() {
    ageModal.classList.remove('is-open');
    document.body.style.overflow = '';
    pendingAdd = null;
    lastFocus?.focus?.();
  }

  $('[data-age-confirm]').addEventListener('click', () => {
    const queued = pendingAdd;          // closeAgeGate() clears pendingAdd, so hold it
    ageVerified = true;
    localStorage.setItem(STORE_AGE, '1');
    closeAgeGate();
    paintAgeState();
    if (queued) addToCart(queued);
    else toast('Age confirmed for this session');
  });
  $$('[data-age-open]').forEach((b) => b.addEventListener('click', openAgeGate));
  $$('[data-age-close]').forEach((b) => b.addEventListener('click', closeAgeGate));

  /* ── Checkout ────────────────────────────────────────────────────────── */
  const receiptModal = $('[data-receipt-modal]');
  const receiptBody = $('[data-receipt-body]');
  const receiptTitle = $('[data-receipt-title]');
  const receiptSummary = $('[data-receipt-summary]');
  const receiptStatus = $('[data-receipt-status]');

  function openReceipt() {
    if (drawer.classList.contains('is-open')) closeCart();
    receiptModal.classList.add('is-open');
    document.body.style.overflow = 'hidden';
    setTimeout(() => $('[data-receipt-close]').focus(), 60);
  }
  function closeReceipt() {
    receiptModal.classList.remove('is-open');
    document.body.style.overflow = '';
  }
  $$('[data-receipt-close]').forEach((b) => b.addEventListener('click', closeReceipt));

  /* The same page is deployed two ways: `python3 server.py` (JSON at /api/checkout)
     and static hosting with a PHP shim (/api/checkout.php). Try in order and only
     accept a real JSON answer, so an HTML 404 page can never masquerade as a result. */
  const declaredApi = $('meta[name="fv-api"]')?.content;
  const CHECKOUT_ENDPOINTS = [...new Set(
    [declaredApi, '/api/checkout', '/api/checkout.php'].filter(Boolean)
  )];

  async function requestCheckout(body) {
    const tried = [];
    for (const url of CHECKOUT_ENDPOINTS) {
      let res;
      try {
        res = await fetch(url, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body)
        });
      } catch (err) {
        tried.push({ url, status: 0, detail: String(err) });
        continue;
      }
      const contentType = res.headers.get('content-type') || 'unknown';
      const text = await res.text();
      let data = null;
      try { data = JSON.parse(text); } catch { /* HTML error page, keep looking */ }
      if (data && typeof data === 'object') return { status: res.status, data, url };
      tried.push({ url, status: res.status, content_type: contentType });
    }
    return { status: 0, data: null, tried };
  }

  $('[data-checkout]').addEventListener('click', async () => {
    const body = payload();
    const btn = $('[data-checkout]');
    btn.disabled = true;
    btn.textContent = 'Pricing…';
    try {
      const result = await requestCheckout(body);

      if (!result.data) {
        /* Nothing on this host answers the API. Say exactly what answered instead. */
        receiptBody.classList.add('is-error');
        receiptBody.textContent = JSON.stringify({
          error: {
            code: 'api_unavailable',
            message: 'The storefront is served as static files, so no server is there to price the basket.',
            tried: result.tried,
            hint: 'Upload api/checkout.php next to index.html (see dist/), or run python3 server.py locally.'
          }
        }, null, 2);
        receiptTitle.textContent = 'No API at this address';
        receiptSummary.textContent = 'The page loaded, but the checkout call came back as a web page instead of JSON, which is what static hosting does with an unknown path.';
        receiptStatus.textContent = result.tried.map((t) => `HTTP ${t.status}`).join(' / ') || 'HTTP —';
        openReceipt();
        return;
      }

      const { status, data } = result;
      receiptBody.classList.toggle('is-error', status < 200 || status >= 300);
      receiptBody.textContent = JSON.stringify(data, null, 2);
      receiptStatus.textContent = `HTTP ${status}`;
      if (status >= 200 && status < 300) {
        receiptTitle.textContent = `Session ${data.checkout_session?.id ?? ''}`.trim();
        const total = data.checkout_session?.total;
        receiptSummary.textContent = total
          ? `Priced at ${money(total)} including ${money(data.checkout_session.tax_total || 0)} VAT. This is a test shop: nothing was charged and nothing ships.`
          : 'Session created.';
        toast('Session priced');
      } else {
        receiptTitle.textContent = 'Rejected by the counter';
        receiptSummary.textContent = data.error?.message || 'The counter refused this basket.';
        if (data.error?.code === 'age_verification_required') {
          pendingAdd = null;
          setTimeout(openAgeGate, 240);
        }
      }
      openReceipt();
    } finally {
      btn.disabled = cartLines().length === 0;
      btn.textContent = 'Checkout';
    }
  });

  /* ── Copy the live UCP profile ───────────────────────────────────────── */
  $('[data-copy-ucp]')?.addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    try {
      const data = await fetch('/.well-known/ucp').then((r) => r.json());
      await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
      const old = btn.textContent;
      btn.textContent = 'Copied';
      setTimeout(() => { btn.textContent = old; }, 1600);
    } catch {
      toast('Copy failed — open /.well-known/ucp directly');
    }
  });

  /* ── Mobile menu ─────────────────────────────────────────────────────── */
  const menu = $('[data-mobile-menu]');
  const menuBtn = $('[data-menu-open]');
  menuBtn.addEventListener('click', () => {
    menu.classList.add('is-open');
    menu.setAttribute('aria-hidden', 'false');
    menuBtn.setAttribute('aria-expanded', 'true');
    document.body.style.overflow = 'hidden';
  });
  const closeMenu = () => {
    menu.classList.remove('is-open');
    menu.setAttribute('aria-hidden', 'true');
    menuBtn.setAttribute('aria-expanded', 'false');
    document.body.style.overflow = '';
  };
  $('[data-menu-close]').addEventListener('click', closeMenu);
  $$('[data-menu-link]').forEach((a) => a.addEventListener('click', closeMenu));

  /* ── Keyboard ────────────────────────────────────────────────────────── */
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (ageModal.classList.contains('is-open')) return closeAgeGate();
    if (receiptModal.classList.contains('is-open')) return closeReceipt();
    if (drawer.classList.contains('is-open')) return closeCart();
    if (menu.classList.contains('is-open')) return closeMenu();
  });

  /* ── Boot ────────────────────────────────────────────────────────────── */
  paintAgeState();
  renderCart();
})();
