// script.js

// Mobile menu toggle
const mobileToggle = document.querySelector('.mobile-menu-toggle');
const nav = document.querySelector('.nav-links');
mobileToggle.addEventListener('click', () => {
  nav.style.display = nav.style.display === 'flex' ? 'none' : 'flex';
});

// Smooth scroll for all internal links
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
  anchor.addEventListener('click', function (e) {
    e.preventDefault();
    const id = this.getAttribute('href').substr(1);
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth' });
    }
    if (window.innerWidth < 900) {
      nav.style.display = 'none'; // close mobile menu on link click
    }
  });
});

// Form validation and success message (only present on index.html)
const form = document.getElementById('leadRequestForm');
const successMsg = document.getElementById('formSuccess');

if (form) {
  form.addEventListener('submit', function(e) {
    e.preventDefault();
    // Clear previous errors
    form.querySelectorAll('.error-msg').forEach(el => el.textContent = '');
    let valid = true;

    // Validate required fields: name, email, company, companyType, marketArea
    ['name', 'email', 'company', 'companyType', 'marketArea'].forEach(id => {
      const input = form.querySelector(`#${id}`);
      if (!input.value.trim()) {
        setError(input, 'This field is required.');
        valid = false;
      } else if (id === 'email' && !validateEmail(input.value.trim())) {
        setError(input, 'Please enter a valid email address.');
        valid = false;
      }
    });

    if (valid) {
      successMsg.style.display = 'block';
      form.reset();
    } else {
      successMsg.style.display = 'none';
    }
  });
}

function setError(input, message) {
  const errMsg = input.nextElementSibling;
  if (errMsg) {
    errMsg.textContent = message;
  }
}

function validateEmail(email) {
  const re = /^[\w-.]+@[\w-]+\.[a-z]{2,}$/i;
  return re.test(email);
}
