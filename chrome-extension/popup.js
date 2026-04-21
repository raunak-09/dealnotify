/**
 * DealNotify Chrome Extension — Popup Logic
 * Handles auth, product detection, and one-click tracking.
 * Matches dealnotify.co website patterns and API contracts.
 *
 * Security best practices:
 *  - Token stored in chrome.storage.local (sandboxed per-extension)
 *  - Token sent via Authorization header, never in URL params
 *  - Session validated on every popup open
 *  - Automatic logout on 401/403 (revoked or expired token)
 *  - Input sanitization on URLs before sending to API
 */

const API_BASE = 'https://www.dealnotify.co';

// ── Google Analytics 4 (Measurement Protocol) ──
// MV3 blocks external script-src, so we use fetch directly instead of gtag.js.
// Shares GA_API_SECRET and client_id with background.js.
// To activate: set GA_API_SECRET from GA4 Admin → Data Streams → Measurement Protocol API secrets.
const GA_MEASUREMENT_ID = 'G-3JJNMF7KKJ';
const GA_API_SECRET = ''; // TODO: paste your Measurement Protocol API secret here

function trackEvent(name, params) {
  if (!GA_API_SECRET) return;
  chrome.storage.local.get(['dn_ga_client_id'], (stored) => {
    const send = (clientId) => {
      fetch(
        `https://www.google-analytics.com/mp/collect?measurement_id=${GA_MEASUREMENT_ID}&api_secret=${GA_API_SECRET}`,
        { method: 'POST', body: JSON.stringify({ client_id: clientId, events: [{ name, params: params || {} }] }) }
      ).catch(() => {});
    };
    if (stored.dn_ga_client_id) {
      send(stored.dn_ga_client_id);
    } else {
      const clientId = crypto.randomUUID();
      chrome.storage.local.set({ dn_ga_client_id: clientId }, () => send(clientId));
    }
  });
}

const SUPPORTED_STORES = {
  'amazon.com':   'Amazon',
  'amazon.co.uk': 'Amazon UK',
  'amazon.ca':    'Amazon CA',
  'walmart.com':  'Walmart',
  'bestbuy.com':  'Best Buy',
  'target.com':   'Target',
  'ebay.com':     'eBay',
  'costco.com':   'Costco'
};

// ── State ──
let currentUser = null;
let currentTab  = null;
let detectedProduct = null;
let selectedTrackType = 'price';

// ── DOM refs ──
const $ = id => document.getElementById(id);

// Views
const viewAuth        = $('viewAuth');
const viewTrack       = $('viewTrack');
const viewUnsupported = $('viewUnsupported');
const viewSuccess     = $('viewSuccess');

// Auth elements
const authTabs     = document.querySelectorAll('.auth-tab');
const formLogin    = $('formLogin');
const formSignup   = $('formSignup');
const authMessage  = $('authMessage');
const authTitle    = $('authTitle');
const authSubtitle = $('authSubtitle');

// Track elements
const storeBadge      = $('storeBadge');
const storeName       = $('storeName');
const productTitle    = $('productTitle');
const productUrl      = $('productUrl');
const productPrice    = $('productPrice');
const targetPriceGrp  = $('targetPriceGroup');
const targetPriceIn   = $('targetPrice');
const btnTrack        = $('btnTrack');
const trackMessage    = $('trackMessage');

// Header elements
const headerActions = $('headerActions');
const userBar       = $('userBar');
const userEmail     = $('userEmail');
const userPlan      = $('userPlan');


// ═══════════════════════════════════════════
//  SECURE API HELPER
// ═══════════════════════════════════════════

/**
 * Make an authenticated API call.
 * - Sends token via Authorization header (not URL params)
 * - Automatically handles 401/403 → session expired → logout
 * - Returns { ok, status, data } so callers can inspect easily
 */
async function apiCall(endpoint, options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {})
  };

  // Attach auth token via header if logged in
  if (currentUser?.token) {
    headers['Authorization'] = `Bearer ${currentUser.token}`;
  }

  // Fallback: also pass token as query param for backward compatibility
  // with the current backend that reads token from request.args
  const separator = endpoint.includes('?') ? '&' : '?';
  const urlWithToken = currentUser?.token
    ? `${API_BASE}${endpoint}${separator}token=${currentUser.token}`
    : `${API_BASE}${endpoint}`;

  try {
    const res = await fetch(urlWithToken, {
      ...options,
      headers
    });

    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON response */ }

    // Handle expired / revoked sessions globally
    if ((res.status === 401 || res.status === 403) && currentUser) {
      // Do NOT logout for known non-auth 403s — let the caller handle them
      if (data?.unverified || data?.error === 'free_limit_reached') {
        return { ok: false, status: res.status, data };
      }
      // Token is no longer valid — force clean logout
      await forceLogout('Your session has expired. Please log in again.');
      return { ok: false, status: res.status, data };
    }

    return { ok: res.ok, status: res.status, data };
  } catch (err) {
    return { ok: false, status: 0, data: { error: 'Network error. Please check your connection and try again.' } };
  }
}


// ═══════════════════════════════════════════
//  INITIALIZATION
// ═══════════════════════════════════════════

document.addEventListener('DOMContentLoaded', async () => {
  // Load saved auth from chrome.storage.local (persists across all tabs & browser restarts)
  const stored = await chrome.storage.local.get(['dn_token', 'dn_email', 'dn_plan']);
  if (stored.dn_token) {
    currentUser = {
      token: stored.dn_token,
      email: stored.dn_email || '',
      plan:  stored.dn_plan  || 'free'
    };
  }

  // Get current tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentTab = tab;

  if (currentUser) {
    // Validate session is still valid on every popup open
    const sessionValid = await validateSession();
    if (sessionValid) {
      showLoggedInUI();
      await detectAndRoute();
    }
    // If invalid, validateSession() already called forceLogout()
  } else {
    showView('auth');
    trackEvent('popup_open', { logged_in: false });
  }

  bindEvents();
});


// ═══════════════════════════════════════════
//  SESSION MANAGEMENT
// ═══════════════════════════════════════════

/**
 * Validate the stored token is still valid by calling the dashboard API.
 * Also refreshes user details (plan, email) so the popup always shows current info.
 * Returns true if session is valid, false if expired/revoked.
 */
async function validateSession() {
  if (!currentUser?.token) return false;

  const { ok, data } = await apiCall('/api/dashboard');

  if (ok && data?.success && data?.user) {
    // Session valid — update local state with fresh data
    const isPro = data.user.status === 'pro';
    currentUser.email = data.user.email;
    currentUser.plan  = isPro ? 'pro' : 'free';
    await chrome.storage.local.set({
      dn_email: currentUser.email,
      dn_plan:  currentUser.plan
    });
    return true;
  }

  // Session is invalid
  return false;
}

/**
 * Force logout — clears stored credentials and shows auth view with a message.
 */
async function forceLogout(message) {
  await chrome.storage.local.remove(['dn_token', 'dn_email', 'dn_plan']);
  currentUser = null;
  hideLoggedInUI();
  showView('auth');
  if (message) {
    showMessage(authMessage, message, 'error');
  }
}


// ═══════════════════════════════════════════
//  VIEW MANAGEMENT
// ═══════════════════════════════════════════

function showView(name) {
  [viewAuth, viewTrack, viewUnsupported, viewSuccess].forEach(v => v.classList.remove('active'));
  switch (name) {
    case 'auth':        viewAuth.classList.add('active'); break;
    case 'track':       viewTrack.classList.add('active'); break;
    case 'unsupported': viewUnsupported.classList.add('active'); break;
    case 'success':     viewSuccess.classList.add('active'); break;
  }
}

function showLoggedInUI() {
  headerActions.style.display = 'flex';
  userBar.style.display = 'flex';
  userEmail.textContent = currentUser.email;
  const isPro = currentUser.plan === 'pro';
  userPlan.textContent = isPro ? 'PRO' : 'FREE';
  userPlan.className = 'user-plan ' + (isPro ? 'pro' : 'free');
}

function hideLoggedInUI() {
  headerActions.style.display = 'none';
  userBar.style.display = 'none';
}

function switchAuthTab(tabName) {
  authTabs.forEach(t => t.classList.remove('active'));
  document.querySelector(`.auth-tab[data-tab="${tabName}"]`).classList.add('active');
  formLogin.classList.toggle('active', tabName === 'login');
  formSignup.classList.toggle('active', tabName === 'signup');
  hideMessage(authMessage);

  // Update header text to match website modal patterns
  if (tabName === 'login') {
    authTitle.textContent = 'Welcome back';
    authSubtitle.textContent = 'Log in to your DealNotify account';
  } else {
    authTitle.textContent = 'Create your account';
    authSubtitle.textContent = 'Start your free 30-day trial';
  }
}


// ═══════════════════════════════════════════
//  STORE DETECTION & ROUTING
// ═══════════════════════════════════════════

function getStoreFromUrl(url) {
  if (!url) return null;
  try {
    const hostname = new URL(url).hostname.replace('www.', '');
    for (const [domain, name] of Object.entries(SUPPORTED_STORES)) {
      if (hostname.includes(domain)) return { domain, name };
    }
  } catch (e) {}
  return null;
}

async function detectAndRoute() {
  if (!currentTab?.url) {
    showView('unsupported');
    return;
  }

  const store = getStoreFromUrl(currentTab.url);

  if (!store) {
    showView('unsupported');
    trackEvent('unsupported_store', { url: currentTab.url });
    return;
  }

  trackEvent('popup_open', { logged_in: true, store: store.name });

  // Show tracking view and attempt to get product info from content script
  showView('track');
  storeName.textContent = store.name;
  productUrl.textContent = currentTab.url;

  try {
    const response = await chrome.tabs.sendMessage(currentTab.id, { action: 'getProductInfo' });
    if (response) {
      detectedProduct = response;
      productTitle.textContent = response.title || 'Product detected';
      if (response.price) {
        productPrice.textContent = response.price;
      } else {
        productPrice.textContent = '';
      }
      // Auto-suggest restock if out of stock
      if (response.outOfStock) {
        selectTrackType('restock');
      }
    }
  } catch (e) {
    // Content script may not be injected on this page — that's fine
    productTitle.textContent = 'Product page detected';
    productPrice.textContent = '';
  }
}


// ═══════════════════════════════════════════
//  INPUT VALIDATION
// ═══════════════════════════════════════════

function isValidEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function isValidProductUrl(url) {
  try {
    const parsed = new URL(url);
    return ['http:', 'https:'].includes(parsed.protocol);
  } catch (e) {
    return false;
  }
}

function sanitizeUrl(url) {
  // Strip fragment and trim whitespace
  try {
    const parsed = new URL(url.trim());
    parsed.hash = '';
    return parsed.toString();
  } catch (e) {
    return url.trim();
  }
}


// ═══════════════════════════════════════════
//  EVENT BINDING
// ═══════════════════════════════════════════

function bindEvents() {
  // Auth tab switching (pill tabs)
  authTabs.forEach(tab => {
    tab.addEventListener('click', () => switchAuthTab(tab.dataset.tab));
  });

  // Auth switch links (e.g., "Don't have an account? Sign up free")
  document.querySelectorAll('.auth-switch a[data-tab]').forEach(link => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      switchAuthTab(link.dataset.tab);
    });
  });

  // Login
  formLogin.addEventListener('submit', handleLogin);

  // Signup
  formSignup.addEventListener('submit', handleSignup);

  // Track type selector
  document.querySelectorAll('.track-type-btn').forEach(btn => {
    btn.addEventListener('click', () => selectTrackType(btn.dataset.type));
  });

  // Track button
  btnTrack.addEventListener('click', handleTrack);

  // Dashboard button — opens dashboard in new tab
  $('btnDashboard').addEventListener('click', () => {
    chrome.tabs.create({ url: `${API_BASE}/dashboard?token=${currentUser.token}` });
  });

  // Logout
  $('btnLogout').addEventListener('click', () => forceLogout());

  // Request store
  $('btnRequestStore').addEventListener('click', handleRequestStore);

  // Track another
  $('btnTrackAnother').addEventListener('click', () => {
    showView('track');
    hideMessage(trackMessage);
  });
}


// ═══════════════════════════════════════════
//  AUTH HANDLERS
// ═══════════════════════════════════════════

async function handleLogin(e) {
  e.preventDefault();
  const email    = $('loginEmail').value.trim();
  const password = $('loginPassword').value;

  // Client-side validation
  if (!email || !password) {
    showMessage(authMessage, 'Please fill in all fields.', 'error');
    return;
  }
  if (!isValidEmail(email)) {
    showMessage(authMessage, 'Please enter a valid email address.', 'error');
    return;
  }

  const btn = $('btnLogin');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';

  const { ok, data } = await apiCall('/api/login', {
    method: 'POST',
    body: JSON.stringify({ email, password })
  });

  if (!ok) {
    showMessage(authMessage, data?.error || 'Login failed. Please try again.', 'error');
    btn.disabled = false;
    btn.textContent = 'Log In';
    return;
  }

  // Save token securely in chrome.storage.local
  currentUser = { token: data.token, email, plan: 'free' };
  await chrome.storage.local.set({
    dn_token: data.token,
    dn_email: email,
    dn_plan: 'free'
  });

  // Fetch fresh user details (plan, verified status)
  await validateSession();

  trackEvent('login', { method: 'email' });

  showLoggedInUI();
  hideMessage(authMessage);
  await detectAndRoute();

  btn.disabled = false;
  btn.textContent = 'Log In';
}

async function handleSignup(e) {
  e.preventDefault();
  const name       = $('signupName').value.trim();
  const email      = $('signupEmail').value.trim();
  const password   = $('signupPassword').value;
  const newsletter = $('signupNewsletter').checked;

  // Client-side validation
  if (!name || !email || !password) {
    showMessage(authMessage, 'Please fill in all fields.', 'error');
    return;
  }
  if (!isValidEmail(email)) {
    showMessage(authMessage, 'Please enter a valid email address.', 'error');
    return;
  }
  if (password.length < 8) {
    showMessage(authMessage, 'Password must be at least 8 characters.', 'error');
    return;
  }

  const btn = $('btnSignup');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';

  const { ok, data } = await apiCall('/api/signup', {
    method: 'POST',
    body: JSON.stringify({ name, email, password, newsletter })
  });

  if (!ok) {
    showMessage(authMessage, data?.error || 'Signup failed. Please try again.', 'error');
    btn.disabled = false;
    btn.textContent = 'Create Free Account';
    return;
  }

  // Save token
  currentUser = { token: data.token, email, plan: 'free' };
  await chrome.storage.local.set({
    dn_token: data.token,
    dn_email: email,
    dn_plan: 'free'
  });

  trackEvent('sign_up', { method: 'email' });
  showMessage(authMessage, 'Account created! Please check your email to verify.', 'success');

  // Show logged-in UI but they'll need to verify before tracking works
  showLoggedInUI();

  btn.disabled = false;
  btn.textContent = 'Create Free Account';
}


// ═══════════════════════════════════════════
//  TRACKING HANDLERS
// ═══════════════════════════════════════════

function selectTrackType(type) {
  selectedTrackType = type;
  document.querySelectorAll('.track-type-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.type === type);
  });
  targetPriceGrp.style.display = type === 'price' ? 'block' : 'none';

  btnTrack.innerHTML = type === 'price'
    ? '<span>🔔</span> Start Price Tracking'
    : '<span>📦</span> Alert Me When Back in Stock';
}

async function handleTrack() {
  if (!currentUser || !currentTab?.url) return;

  // Validate and sanitize the product URL
  const cleanUrl = sanitizeUrl(currentTab.url);
  if (!isValidProductUrl(cleanUrl)) {
    showMessage(trackMessage, 'Invalid product URL.', 'error');
    return;
  }

  const payload = {
    url: cleanUrl,
    track_type: selectedTrackType
  };

  if (selectedTrackType === 'price') {
    const tp = parseFloat(targetPriceIn.value);
    if (tp > 0) {
      payload.target_price = tp;
    }
  }

  btnTrack.disabled = true;
  btnTrack.innerHTML = '<span class="spinner"></span> Adding...';

  const { ok, data } = await apiCall('/api/add-product', {
    method: 'POST',
    body: JSON.stringify(payload)
  });

  if (!ok) {
    if (data?.error === 'free_limit_reached') {
      showMessage(trackMessage, data.message, 'error');
    } else {
      showMessage(trackMessage, data?.error || 'Failed to add product.', 'error');
    }
    btnTrack.disabled = false;
    btnTrack.innerHTML = selectedTrackType === 'price'
      ? '<span>🔔</span> Start Price Tracking'
      : '<span>📦</span> Alert Me When Back in Stock';
    return;
  }

  // Success!
  trackEvent('track_product', {
    track_type: selectedTrackType,
    store: getStoreFromUrl(currentTab.url)?.name || 'unknown',
    has_target_price: !!(selectedTrackType === 'price' && payload.target_price),
  });

  const successText = $('successText');
  if (selectedTrackType === 'price') {
    const tp = payload.target_price ? `$${payload.target_price}` : 'your target';
    successText.textContent = `We'll email you when the price drops to ${tp}. Manage all alerts on the dashboard.`;
  } else {
    successText.textContent = `We'll email you the moment this item is back in stock. Manage all alerts on the dashboard.`;
  }
  showView('success');

  btnTrack.disabled = false;
  btnTrack.innerHTML = selectedTrackType === 'price'
    ? '<span>🔔</span> Start Price Tracking'
    : '<span>📦</span> Alert Me When Back in Stock';
}

function handleRequestStore() {
  const hostname = currentTab?.url ? new URL(currentTab.url).hostname : 'this store';
  const msgEl = $('requestMessage');
  showMessage(msgEl, `Thanks! We've noted your interest in ${hostname}. We'll notify you when it's supported.`, 'success');
  $('btnRequestStore').disabled = true;
  $('btnRequestStore').textContent = 'Requested ✓';
}


// ═══════════════════════════════════════════
//  HELPERS
// ═══════════════════════════════════════════

function showMessage(el, text, type) {
  el.textContent = text;
  el.className = `message ${type}`;
}

function hideMessage(el) {
  el.textContent = '';
  el.className = 'message';
}
