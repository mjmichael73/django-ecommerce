/*
 * Order-detail "Print order" handler, moved out of the template so the
 * Content-Security-Policy can keep script-src at 'self' without
 * 'unsafe-inline' (card f3d54f76). See docs/security.md.
 */
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.js-print-order').forEach(function (link) {
        link.addEventListener('click', function (event) {
            event.preventDefault();
            window.print();
        });
    });
});
