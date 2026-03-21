// HTMX redirect handler: listens for HX-Redirect header and performs a client-side redirect
(function() {
  document.body.addEventListener('htmx:afterOnLoad', function(evt) {
    var xhr = evt.detail.xhr;
    var redirectUrl = xhr.getResponseHeader('HX-Redirect');
    if (redirectUrl) {
      window.location.href = redirectUrl;
    }
  });
})();
