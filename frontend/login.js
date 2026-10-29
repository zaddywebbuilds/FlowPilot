/* FlowPilot login and first-run password setup */

const form = document.getElementById('authForm');
const password = document.getElementById('password');
const confirm = document.getElementById('confirm');
const confirmField = document.getElementById('confirmField');
const errorBox = document.getElementById('authError');
const submit = document.getElementById('authSubmit');

let mode = 'login';

(function initTheme() {
  try {
    document.documentElement.dataset.theme = localStorage.getItem('fp-theme') || 'light';
  } catch {
    document.documentElement.dataset.theme = 'light';
  }
})();

function showError(message) {
  errorBox.textContent = message;
  errorBox.style.display = message ? 'block' : 'none';
}

async function detectMode() {
  try {
    const res = await fetch('/api/auth/status');
    const status = await res.json();

    if (status.authenticated) {
      location.href = '/';
      return;
    }

    if (!status.configured) {
      mode = 'setup';
      document.getElementById('authTitle').textContent = 'Create a password';
      document.getElementById('authSub').textContent =
        'First run. Choose the password you will use to reach this dashboard.';
      document.getElementById('passwordLabel').textContent = 'New password';
      document.getElementById('passwordHint').textContent = 'At least 8 characters.';
      password.setAttribute('autocomplete', 'new-password');
      confirmField.style.display = 'block';
      confirm.required = true;
      submit.textContent = 'Create password';
    }
  } catch {
    showError('Cannot reach the server. Is FlowPilot running?');
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError('');

  if (mode === 'setup') {
    if (password.value.length < 8) return showError('Password must be at least 8 characters.');
    if (password.value !== confirm.value) return showError('Those passwords do not match.');
  }

  submit.disabled = true;
  submit.textContent = mode === 'setup' ? 'Creating...' : 'Signing in...';

  try {
    const res = await fetch(`/api/auth/${mode}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: password.value }),
    });

    if (res.ok) {
      location.href = '/';
      return;
    }

    const body = await res.json().catch(() => ({}));
    showError(body.detail || 'That did not work.');
  } catch {
    showError('Cannot reach the server.');
  }

  submit.disabled = false;
  submit.textContent = mode === 'setup' ? 'Create password' : 'Sign in';
  password.select();
});

detectMode();
