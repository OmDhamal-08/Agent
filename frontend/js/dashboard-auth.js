/**
 * dashboard-auth.js — Merchant Dashboard Authentication Gating.
 *
 * Handles JWT token storage in sessionStorage, login/signup form flows,
 * Authorization header injection via authFetch, and session validation.
 */

const ADMIN_TOKEN_KEY = 'admin_token';
const ADMIN_EMAIL_KEY = 'admin_email';

/**
 * Custom fetch wrapper that injects Bearer token and handles 401 unauthorized.
 */
async function authFetch(url, options = {}) {
  const token = sessionStorage.getItem(ADMIN_TOKEN_KEY);
  const headers = {
    ...(options.headers || {}),
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(url, { ...options, headers });

  if (response.status === 401) {
    // Token is invalid or expired
    sessionStorage.removeItem(ADMIN_TOKEN_KEY);
    sessionStorage.removeItem(ADMIN_EMAIL_KEY);
    showAuthView();
    throw new Error('Unauthorized');
  }

  return response;
}



function showAuthView() {
  const authContainer = document.getElementById('auth-container');
  const dashboardContent = document.getElementById('dashboard-content');
  const userSection = document.getElementById('admin-user-section');

  if (authContainer) authContainer.style.display = 'flex';
  if (dashboardContent) dashboardContent.style.display = 'none';
  if (userSection) userSection.style.display = 'none';
}

function showDashboardView(email) {
  const authContainer = document.getElementById('auth-container');
  const dashboardContent = document.getElementById('dashboard-content');
  const userSection = document.getElementById('admin-user-section');
  const emailBadge = document.getElementById('admin-email-badge');

  if (authContainer) authContainer.style.display = 'none';
  if (dashboardContent) dashboardContent.style.display = 'block';
  if (userSection) userSection.style.display = 'flex';
  if (emailBadge) emailBadge.textContent = email || 'Admin';

  if (typeof initDashboard === 'function') {
    initDashboard();
  }
}

function toggleAuthMode(mode) {
  const loginForm = document.getElementById('login-form');
  const signupForm = document.getElementById('signup-form');
  const authMsg = document.getElementById('auth-error-msg');
  if (authMsg) authMsg.textContent = '';

  if (mode === 'signup') {
    if (loginForm) loginForm.style.display = 'none';
    if (signupForm) signupForm.style.display = 'block';
  } else {
    if (loginForm) loginForm.style.display = 'block';
    if (signupForm) signupForm.style.display = 'none';
  }
}



async function handleLogin(e) {
  if (e) e.preventDefault();
  const email = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;
  const msgEl = document.getElementById('auth-error-msg');
  const btn = document.getElementById('login-submit-btn');

  if (!email || !password) {
    if (msgEl) msgEl.textContent = 'Please enter both email and password.';
    return;
  }

  if (btn) btn.disabled = true;
  if (msgEl) {
    msgEl.style.color = 'var(--text-muted)';
    msgEl.textContent = 'Authenticating...';
  }

  try {
    const res = await fetch('/api/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });

    const data = await res.json();

    if (!res.ok) {
      if (msgEl) {
        msgEl.style.color = 'var(--danger)';
        msgEl.textContent = data.detail || 'Invalid email or password.';
      }
      return;
    }

    // Store in sessionStorage (per browser tab/session)
    sessionStorage.setItem(ADMIN_TOKEN_KEY, data.token);
    sessionStorage.setItem(ADMIN_EMAIL_KEY, data.email);

    if (msgEl) msgEl.textContent = '';
    showDashboardView(data.email);
  } catch (err) {
    if (msgEl) {
      msgEl.style.color = 'var(--danger)';
      msgEl.textContent = 'Connection error. Please try again.';
    }
    console.error('Login error:', err);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handleSignup(e) {
  if (e) e.preventDefault();
  const email = document.getElementById('signup-email').value.trim();
  const password = document.getElementById('signup-password').value;
  const signupCode = document.getElementById('signup-code').value.trim();
  const msgEl = document.getElementById('auth-error-msg');
  const btn = document.getElementById('signup-submit-btn');

  if (!email || !password || !signupCode) {
    if (msgEl) {
      msgEl.style.color = 'var(--danger)';
      msgEl.textContent = 'All fields are required.';
    }
    return;
  }

  if (password.length < 8) {
    if (msgEl) {
      msgEl.style.color = 'var(--danger)';
      msgEl.textContent = 'Password must be at least 8 characters long.';
    }
    return;
  }

  if (btn) btn.disabled = true;
  if (msgEl) {
    msgEl.style.color = 'var(--text-muted)';
    msgEl.textContent = 'Creating account...';
  }

  try {
    const res = await fetch('/api/admin/signup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, signup_code: signupCode }),
    });

    const data = await res.json();

    if (!res.ok) {
      if (msgEl) {
        msgEl.style.color = 'var(--danger)';
        msgEl.textContent = data.detail || 'Failed to create account.';
      }
      return;
    }

    // Auto switch to login view and prefill email
    toggleAuthMode('login');
    const loginEmailEl = document.getElementById('login-email');
    const loginPasswordEl = document.getElementById('login-password');
    if (loginEmailEl) loginEmailEl.value = email;
    if (loginPasswordEl) loginPasswordEl.value = '';

    if (msgEl) {
      msgEl.style.color = 'var(--success)';
      msgEl.textContent = '✅ Account created! You can now log in.';
    }
  } catch (err) {
    if (msgEl) {
      msgEl.style.color = 'var(--danger)';
      msgEl.textContent = 'Connection error. Please try again.';
    }
    console.error('Signup error:', err);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function handleLogout() {
  if (typeof _refreshInterval !== 'undefined' && _refreshInterval) {
    clearInterval(_refreshInterval);
    _refreshInterval = null;
  }
  sessionStorage.removeItem(ADMIN_TOKEN_KEY);
  sessionStorage.removeItem(ADMIN_EMAIL_KEY);
  showAuthView();
}



async function checkAdminAuth() {
  const token = sessionStorage.getItem(ADMIN_TOKEN_KEY);
  const email = sessionStorage.getItem(ADMIN_EMAIL_KEY);

  if (!token) {
    showAuthView();
    return;
  }

  try {
    const res = await fetch('/api/admin/me', {
      headers: { 'Authorization': `Bearer ${token}` }
    });

    if (res.ok) {
      const data = await res.json().catch(() => ({}));
      showDashboardView(data.email || email);
    } else if (res.status === 401 || res.status === 403) {
      sessionStorage.removeItem(ADMIN_TOKEN_KEY);
      sessionStorage.removeItem(ADMIN_EMAIL_KEY);
      showAuthView();
    }
  } catch (err) {
    console.warn('Could not verify admin session:', err);
    // Don't wipe token immediately on temporary network failure
  }
}

document.addEventListener('DOMContentLoaded', () => {
  checkAdminAuth();
});
