/*
 * Infinite scroll / "Load more" for the product catalog (card 3agb8235).
 *
 * A sentinel element at the end of the product grid carries the next page
 * URL in ``data-next-url``. When the user scrolls near the sentinel (or
 * clicks the "Load more" button), we fetch that URL via ``fetch()``, parse
 * the returned HTML, extract the new ``.product-card`` elements, re-run the
 * lazy-load wiring on them, append them to the grid, and update the URL via
 * ``history.pushState``.
 *
 * When there are no more pages the sentinel's ``data-next-url`` is empty and
 * the button is hidden. The pagination nav is hidden on the initial page
 * load (CSS) and only the lazy-loaded cards appear via AJAX.
 */
(function () {
    'use strict';

    var ROOT_MARGIN = '200px';
    var GRID_SELECTOR = '.product-grid';
    var CARD_SELECTOR = '.product-card';
    var SENTINEL_ID = 'infinite-scroll-sentinel';
    var BUTTON_ID = 'infinite-scroll-load-more';

    var state = {
        loading: false,
        loadingFinished: false,
        observer: null,
    };

    /** Resolve the product grid container (or bail). */
    function getGrid() {
        return document.querySelector(GRID_SELECTOR);
    }

    /** Resolve the sentinel element (or bail). */
    function getSentinel() {
        return document.getElementById(SENTINEL_ID);
    }

    /** Resolve the load-more button (may not exist if only sentinel is used). */
    function getButton() {
        return document.getElementById(BUTTON_ID);
    }

    /**
     * Extract ``.product-card`` elements from a parsed HTML document.
     * Returns an array of cloned nodes so the parser DOM can be discarded.
     */
    function extractCards(htmlDoc) {
        return Array.prototype.slice.call(htmlDoc.querySelectorAll(CARD_SELECTOR));
    }

    /** Append fetched cards to the grid, re-wiring lazy images. */
    function appendCards(cards) {
        var grid = getGrid();
        if (!grid) return;

        var fragment = document.createDocumentFragment();
        cards.forEach(function (card) {
            fragment.appendChild(card);
        });
        grid.appendChild(fragment);

        // Re-initialize lazy loading on the newly added images.
        if (window.initLazyLoad) {
            window.initLazyLoad();
        } else if (window.lazyLoadInit) {
            window.lazyLoadInit();
        }
    }

    /** Enable or disable the load-more button visually. */
    function setButtonLoading(isBusy) {
        var button = getButton();
        if (!button) return;
        button.disabled = isBusy;
        button.textContent = isBusy ? 'Loading…' : 'Load more';
    }

    function setButtonHidden(hidden) {
        var button = getButton();
        if (!button) return;
        if (hidden) {
            button.style.display = 'none';
        } else {
            button.style.removeProperty('display');
        }
    }

    /**
     * Fetch the next page URL, extract cards, append them, and update state.
     * Returns a promise that resolves once the DOM has been updated.
     */
    function loadNextPage() {
        if (state.loading || state.loadingFinished) {
            return Promise.resolve();
        }

        var sentinel = getSentinel();
        if (!sentinel) {
            state.loadingFinished = true;
            return Promise.resolve();
        }

        var nextUrl = sentinel.getAttribute('data-next-url');
        if (!nextUrl) {
            state.loadingFinished = true;
            setButtonHidden(true);
            if (state.observer) {
                state.observer.unobserve(sentinel);
            }
            return Promise.resolve();
        }

        state.loading = true;
        setButtonLoading(true);

        return fetch(nextUrl, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('HTTP ' + response.status);
                }
                return response.text();
            })
            .then(function (html) {
                var parser = new DOMParser();
                var doc = parser.parseFromString(html, 'text/html');
                var cards = extractCards(doc);

                if (cards.length === 0) {
                    state.loadingFinished = true;
                    setButtonHidden(true);
                    if (state.observer) {
                        state.observer.unobserve(sentinel);
                    }
                    return;
                }

                appendCards(cards);

                // Update URL via pushState so back-button / bookmarking works.
                var url = new URL(nextUrl, window.location.origin);
                var newPage = url.searchParams.get('page');
                if (newPage) {
                    var basePath = window.location.pathname;
                    var searchParams = new URLSearchParams(window.location.search);
                    searchParams.set('page', newPage);
                    // Preserve all other params (q, facets) but drop stale page.
                    var cleanSearch = searchParams.toString();
                    var newPath = cleanSearch ? basePath + '?' + cleanSearch : basePath;
                    window.history.pushState({ page: newPage }, '', newPath);
                }

                // Update the sentinel with the next page URL from the response.
                var newSentinel = doc.getElementById(SENTINEL_ID);
                if (newSentinel) {
                    sentinel.setAttribute('data-next-url', newSentinel.getAttribute('data-next-url') || '');
                } else {
                    sentinel.setAttribute('data-next-url', '');
                }

                // If no further pages, stop observing.
                if (!sentinel.getAttribute('data-next-url')) {
                    state.loadingFinished = true;
                    setButtonHidden(true);
                    if (state.observer) {
                        state.observer.unobserve(sentinel);
                    }
                }
            })
            .catch(function (err) {
                // On error, un-disable the button so the user can retry.
                setButtonLoading(false);
                // eslint-disable-next-line no-console
                console.error('Infinite scroll error:', err);
            })
            .finally(function () {
                state.loading = false;
                setButtonLoading(false);
            });
    }

    /**
     * Scroll-driven auto-load: when the sentinel enters the viewport,
     * trigger the next page fetch. The button is the primary trigger for
     * accessibility, but the observer also auto-loads for a smooth experience.
     */
    function initIntersectionObserver() {
        var sentinel = getSentinel();
        if (!sentinel) return;

        // If there's no next URL, don't set up the observer at all.
        if (!sentinel.getAttribute('data-next-url')) {
            setButtonHidden(true);
            return;
        }

        if (!('IntersectionObserver' in window)) {
            // No observer: rely on the button click only.
            setButtonHidden(false);
            return;
        }

        state.observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting && !state.loading && !state.loadingFinished) {
                    loadNextPage();
                }
            });
        }, {
            root: null,
            rootMargin: ROOT_MARGIN,
            threshold: 0,
        });

        state.observer.observe(sentinel);
    }

    /** Wire up the load-more button click. */
    function initButton() {
        var button = getButton();
        if (!button) return;

        button.addEventListener('click', function (e) {
            e.preventDefault();
            loadNextPage();
        });
    }

    function init() {
        // Only initialise on catalog listing pages that have the sentinel.
        if (!getSentinel()) return;

        initButton();
        initIntersectionObserver();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
