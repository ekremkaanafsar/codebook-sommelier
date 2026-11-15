/* Text search: lazy-loads api/search.json on the first keystroke and builds a MiniSearch index.
   window.CatalogSearch.ids(query) resolves to a Map of matching dataset ids to relevance scores (iteration order is best match first), or null for an empty query. */
(function () {
  "use strict";
  var FIELDS = ["name", "acronym", "key_variables", "strengths", "best_used_for", "maintainer"];
  var indexPromise = null;
  var searchUrl = "";

  function load() {
    if (!indexPromise) {
      indexPromise = fetch(searchUrl).then(function (r) {
        if (!r.ok) { throw new Error("search index: HTTP " + r.status); }
        return r.json();
      }).then(function (docs) {
        var ms = new MiniSearch({
          fields: FIELDS,
          storeFields: ["id"],
          searchOptions: { prefix: true, fuzzy: 0.2, boost: { name: 3, acronym: 3 }, combineWith: "AND" }
        });
        ms.addAll(docs);
        return ms;
      });
      indexPromise.catch(function () { indexPromise = null; });
    }
    return indexPromise;
  }

  window.CatalogSearch = {
    configure: function (url) { searchUrl = url; },
    ids: function (query) {
      query = (query || "").trim();
      if (!query) { return Promise.resolve(null); }
      return load().then(function (ms) {
        var set = new Map();
        ms.search(query).forEach(function (hit) { set.set(hit.id, hit.score); });
        return set;
      });
    }
  };
})();
