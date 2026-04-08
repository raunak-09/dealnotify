/**
 * DealNotify Chrome Extension — Background Service Worker
 *
 * Responsibilities:
 *  1. Badge indicator on supported store tabs (green dot)
 *  2. Listen for auth state changes and sync badge/icon accordingly
 *  3. Handle extension install/update — preserve sessions across updates
 */

const API_BASE = 'https://www.dealnotify.co';

const SUPPORTED_DOMAINS = [
  'amazon.com', 'amazon.co.uk', 'amazon.ca',
  'walmart.com', 'bestbuy.com', 'target.com',
  'ebay.com', 'costco.com'
];

// ── Badge Management ──

function isSupportedUrl(url) {
  try {
    const hostname = new URL(url).hostname.replace('www.', '');
    return SUPPORTED_DOMAINS.some(d => hostname.includes(d));
  } catch (e) {
    return false;
  }
}

async function updateBadge(tabId, url) {
  if (isSupportedUrl(url)) {
    await chrome.action.setBadgeText({ text: '●', tabId });
    await chrome.action.setBadgeBackgroundColor({ color: '#22c55e', tabId });
  } else {
    await chrome.action.setBadgeText({ text: '', tabId });
  }
}

// Update badge when user navigates to a supported store
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete' && tab.url) {
    updateBadge(tabId, tab.url);
  }
});

// Update badge when user switches tabs
chrome.tabs.onActivated.addListener(async (activeInfo) => {
  try {
    const tab = await chrome.tabs.get(activeInfo.tabId);
    if (tab.url) {
      updateBadge(tab.id, tab.url);
    }
  } catch (e) { /* tab may not exist */ }
});

// Clear badge when tab is removed
chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.action.setBadgeText({ text: '', tabId });
});


// ── Open popup when widget button is clicked on a product page ──

chrome.runtime.onMessage.addListener((message, sender) => {
  if (message.action === 'openPopup') {
    // Called from content script when user clicks the floating DealNotify widget.
    // chrome.action.openPopup() requires a user gesture — clicking the widget qualifies.
    chrome.action.openPopup().catch(() => {
      // openPopup may fail if the tab is not active — silently ignore
    });
  }
});


// ── Auth State Sync ──

// Listen for storage changes (e.g., login/logout in popup)
// and update the icon title accordingly
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;

  if (changes.dn_token) {
    const loggedIn = !!changes.dn_token.newValue;
    const email = changes.dn_email?.newValue || '';
    chrome.action.setTitle({
      title: loggedIn
        ? `DealNotify — Logged in as ${email}`
        : 'DealNotify — Click to log in'
    });
  }
});


// ── Extension Lifecycle ──

chrome.runtime.onInstalled.addListener(async (details) => {
  if (details.reason === 'install') {
    // First install — set default icon title
    chrome.action.setTitle({ title: 'DealNotify — Click to log in' });
  }

  if (details.reason === 'update') {
    // Extension updated — session persists in chrome.storage.local automatically
    // Just refresh the icon title
    const stored = await chrome.storage.local.get(['dn_token', 'dn_email']);
    if (stored.dn_token) {
      chrome.action.setTitle({
        title: `DealNotify — Logged in as ${stored.dn_email || 'user'}`
      });
    }
  }
});

// On startup, refresh icon title from stored auth
chrome.runtime.onStartup.addListener(async () => {
  const stored = await chrome.storage.local.get(['dn_token', 'dn_email']);
  if (stored.dn_token) {
    chrome.action.setTitle({
      title: `DealNotify — Logged in as ${stored.dn_email || 'user'}`
    });
  } else {
    chrome.action.setTitle({ title: 'DealNotify — Click to log in' });
  }
});
