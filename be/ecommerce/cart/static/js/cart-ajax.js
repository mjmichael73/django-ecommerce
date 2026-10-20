/*
 * cart-ajax.js — AJAX cart updates (card 77ce5899)
 *
 * Intercepts the add-to-cart (product detail + cart update forms) and
 * remove-from-cart forms so they POST via fetch() instead of full-page
 * submission. On success the cart badge, cart total, and (when on the
 * cart detail page) the cart table are patched from the JSON contract.
 *
 * Forms that do *not* carry the ``data-cart-add`` / ``data-cart-remove``
 * markers (e.g. the coupon form, the checkout button) keep their
 * normal full-page behaviour.
 *
 * Requires the CSRF cookie (set by Django's CsrfViewMiddleware) to be
 * sent on every AJAX POST.
 */
(function () {
  'use strict';

  // Only run if the DOM markers exist — pages without cart forms are untouched.
  var hasAddForms = document.querySelectorAll('[data-cart-add]').length > 0;
  var hasRemoveForms = document.querySelectorAll('[data-cart-remove]').length > 0;
  if (!hasAddForms && !hasRemoveForms) return;

  function getCookie(name) {
    var value = '; ' + document.cookie;
    var parts = value.split('; ' + name + '=');
    if (parts.length === 2) return parts.pop().split(';').shift();
  }

  function csrfToken() {
    return getCookie('csrftoken');
  }

  function updateHeader(json) {
    var badge = document.getElementById('cart-badge');
    var total = document.getElementById('cart-total');
    if (badge) {
      badge.textContent = json.total_items;
      badge.classList.remove('cart-badge--empty');
    }
    if (total) {
      total.textContent = '$' + json.total;
      if (json.total_items > 0) {
        total.style.display = '';
      } else {
        total.style.display = 'none';
      }
    }
  }

  function updateCartTable(json) {
    var grandTotal = document.getElementById('cart-grand-total');
    if (grandTotal) {
      grandTotal.textContent = '$' + json.total;
    }
  }

  function showError(message, errors) {
    var existing = document.querySelector('.cart-ajax-error');
    if (existing) existing.remove();

    var box = document.createElement('div');
    box.className = 'cart-ajax-error messages__item messages__item--error';
    box.textContent = message;
    if (errors) {
      var ul = document.createElement('ul');
      Object.keys(errors).forEach(function (field) {
        errors[field].forEach(function (msg) {
          var li = document.createElement('li');
          li.textContent = field + ': ' + msg;
          ul.appendChild(li);
        });
      });
      box.appendChild(ul);
    }

    var main = document.getElementById('content');
    if (main) {
      main.insertBefore(box, main.firstChild);
    }
    // Scroll to the error so the user sees it.
    box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function removeRow(form) {
    var row = form.closest('tr[data-cart-line]');
    if (row) {
      row.remove();
    }
  }

  function isAjaxResponse(resp) {
    return resp.headers.get('Content-Type') &&
      resp.headers.get('Content-Type').indexOf('application/json') !== -1;
  }

  function handleAddSubmit(form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var url = form.action;
      var data = new FormData(form);
      data.append('quantity', form.querySelector('[name="quantity"]').value);

      fetch(url, {
        method: 'POST',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'X-CSRFToken': csrfToken(),
          'Accept': 'application/json',
        },
        body: data,
      }).then(function (resp) {
        return resp.text().then(function (text) {
          var json;
          try {
            json = JSON.parse(text);
          } catch (err) {
            // If parsing fails, just redirect to the cart page
            window.location.href = '/cart/';
            return;
          }
          if (resp.ok && json.ok) {
            updateHeader(json);
            updateCartTable(json);
          } else if (resp.status === 429) {
            showError('Too many requests. Please wait before trying again.');
          } else {
            showError(json.error || 'Could not update cart', json.errors);
          }
        });
      }).catch(function () {
        // Network error — fall back to full-page submit
        window.location.href = form.action;
      });
    });
  }

  function handleRemoveSubmit(form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var url = form.action;
      var data = new FormData(form);

      fetch(url, {
        method: 'POST',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'X-CSRFToken': csrfToken(),
          'Accept': 'application/json',
        },
        body: data,
      }).then(function (resp) {
        return resp.text().then(function (text) {
          var json;
          try {
            json = JSON.parse(text);
          } catch (err) {
            window.location.href = '/cart/';
            return;
          }
          if (resp.ok && json.ok) {
            removeRow(form);
            updateHeader(json);
            updateCartTable(json);
          } else if (resp.status === 429) {
            showError('Too many requests. Please wait before trying again.');
          } else {
            showError(json.error || 'Could not remove item');
          }
        });
      }).catch(function () {
        window.location.href = form.action;
      });
    });
  }

  // Attach handlers to existing forms.
  document.querySelectorAll('[data-cart-add]').forEach(handleAddSubmit);
  document.querySelectorAll('[data-cart-remove]').forEach(handleRemoveSubmit);

  // Also hook the add-to-cart form on product detail pages (the form
  // rendered by product_detail, which does not live in the cart app).
  var detailForm = document.querySelector('form[action*="/cart/add/"]');
  if (detailForm && !detailForm.hasAttribute('data-cart-add')) {
    detailForm.setAttribute('data-cart-add', '');
    handleAddSubmit(detailForm);
  }
})();
