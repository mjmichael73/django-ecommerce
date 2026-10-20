/*
 * Lazy-loaded images via IntersectionObserver (card 3agb8235).
 *
 * Images use ``data-src`` (and optional ``data-srcset``) plus a low-quality
 * placeholder (a tiny blurred thumbnail or a background colour) as their
 * visible ``src``. When the image enters the viewport the observer swaps in
 * the real source, then disconnects for that image so it never fires twice.
 *
 * Falls back to loading every image immediately for browsers without
 * IntersectionObserver support — the visible ``src`` was already set in the
 * template, so images load normally without any JS.
 */
(function () {
    'use strict';

    var THRESHOLD = 0.01;

    /**
     * Promote a single image from lazy to loaded.
     *
     * * Moves ``data-src`` / ``data-srcset`` into ``src`` / ``srcset``.
     * * Removes the placeholder class so the blur/fade transition can finish.
     * * Marks the image loaded and unobserves it.
     */
    function loadImg(img) {
        // srcset first (takes precedence in modern browsers when both present).
        var srcset = img.getAttribute('data-srcset');
        if (srcset) {
            img.setAttribute('srcset', srcset);
            img.removeAttribute('data-srcset');
        }

        var src = img.getAttribute('data-src');
        if (src) {
            img.setAttribute('src', src);
            img.removeAttribute('data-src');
        }

        img.classList.remove('lazy');
        img.classList.add('loaded');
        img.removeAttribute('data-lazy-loaded');
    }

    /**
     * Observer callback: walk the entries and load any image that has
     * intersected the viewport.
     */
    function onIntersection(entries, observer) {
        entries.forEach(function (entry) {
            if (!entry.isIntersecting) {
                return;
            }
            var img = entry.target;
            // Guard against double-load (e.g. observer + manual init).
            if (img.getAttribute('data-lazy-loaded') === 'true' || !img.hasAttribute('data-src')) {
                observer.unobserve(img);
                return;
            }
            img.setAttribute('data-lazy-loaded', 'true');
            loadImg(img);
            observer.unobserve(img);
        });
    }

    /**
     * Immediate fallback for browsers without IntersectionObserver.
     * Loads every lazy image right away — they already have a visible src
     * placeholder, so this just swaps in the real source immediately.
     */
    function loadAllImmediately() {
        var imgs = document.querySelectorAll('img[data-src]');
        imgs.forEach(loadImg);
    }

    function init() {
        // Only target images that opt into lazy loading via ``data-src``.
        var imgs = document.querySelectorAll('img[data-src]');
        if (imgs.length === 0) {
            return;
        }

        if (!('IntersectionObserver' in window)) {
            loadAllImmediately();
            return;
        }

        var observer = new IntersectionObserver(onIntersection, {
            root: null,          // viewport
            rootMargin: '50px',  // start loading a touch before it appears
            threshold: THRESHOLD,
        });

        imgs.forEach(function (img) {
            observer.observe(img);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
