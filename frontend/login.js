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

function asNewPasswordForm(title, sub, label, hint, button) {
  document.getElementById('authTitle').textContent = title;
  document.getElementById('authSub').textContent = sub;
  document.getElementById('passwordLabel').textContent = label;
  document.getElementById('passwordHint').textContent = hint;
  password.setAttribute('autocomplete', 'new-password');
  confirmField.style.display = 'block';
  confirm.required = true;
  submit.textContent = button;
}

async function detectMode() {
  try {
    const res = await fetch('/api/auth/status');
    const status = await res.json();

    if (status.authenticated && status.must_change) {
      mode = 'password';
      asNewPasswordForm(
        'Choose your password',
        'You signed in with a temporary password. Pick your own to continue.',
        'Your new password',
        'At least 8 characters.',
        'Save and continue',
      );
      return;
    }

    if (status.authenticated) {
      location.href = '/';
      return;
    }

    if (!status.configured) {
      mode = 'setup';
      asNewPasswordForm(
        'Create a password',
        'First run. Choose the password you will use to reach this dashboard.',
        'New password',
        'At least 8 characters.',
        'Create password',
      );
    }
  } catch {
    showError('Cannot reach the server. Is FlowPilot running?');
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError('');

  const BUTTONS = {
    setup: ['Create password', 'Creating...'],
    password: ['Save and continue', 'Saving...'],
    login: ['Sign in', 'Signing in...'],
  };

  if (mode !== 'login') {
    if (password.value.length < 8) return showError('Password must be at least 8 characters.');
    if (password.value !== confirm.value) return showError('Those passwords do not match.');
  }

  submit.disabled = true;
  submit.textContent = BUTTONS[mode][1];

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
  submit.textContent = BUTTONS[mode][0];
  password.select();
});

detectMode();
