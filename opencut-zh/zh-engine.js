/* OpenCut 中文汉化引擎（运行时词典替换，不改组件代码） */
(function () {
  "use strict";
  var DICT = null;
  var PLACEHOLDER_DICT = null;
  var VALUE_DICT = null;

  function loadDict() {
    return fetch("/zh-dict.json", { cache: "no-cache" })
      .then(function (r) { return r.json(); })
      .then(function (j) { DICT = j.text || {}; PLACEHOLDER_DICT = j.placeholder || {}; VALUE_DICT = j.value || {}; })
      .catch(function () { DICT = {}; PLACEHOLDER_DICT = {}; VALUE_DICT = {}; });
  }

  function translateTextNode(node) {
    if (!DICT) return;
    var t = node.nodeValue;
    if (!t) return;
    var trimmed = t.trim();
    if (!trimmed) return;
    var hit = DICT[trimmed];
    if (hit && hit !== trimmed) {
      node.nodeValue = t.replace(trimmed, hit);
    }
  }

  function walk(root) {
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (var i = 0; i < nodes.length; i++) translateTextNode(nodes[i]);
  }

  function translateElement(el) {
    if (!PLACEHOLDER_DICT) return;
    var targets = [el];
    try { targets = targets.concat([].slice.call(el.querySelectorAll("input,textarea,[placeholder],[title],[aria-label]"))); } catch (e) {}
    targets.forEach(function (node) {
      ["placeholder", "title", "aria-label"].forEach(function (attr) {
        var v = node.getAttribute && node.getAttribute(attr);
        if (v) {
          var hit = PLACEHOLDER_DICT[v.trim()];
          if (hit && hit !== v.trim()) node.setAttribute(attr, hit);
        }
      });
    });
  }

  function translateValues() {
    if (!VALUE_DICT) return;
    document.querySelectorAll("input,textarea").forEach(function (el) {
      var v = el.value;
      if (!v) return;
      var hit = VALUE_DICT[v.trim()];
      if (hit && el !== document.activeElement) el.value = hit;
    });
  }

  function applyAll() {
    walk(document.body || document.documentElement);
    document.querySelectorAll("input,textarea").forEach(translateElement);
    translateValues();
  }

  var mo = new MutationObserver(function (mutations) {
    if (!DICT) return;
    var seen = null;
    function once(n) {
      if (!seen) seen = new Set();
      if (seen.has(n)) return false;
      seen.add(n); return true;
    }
    for (var i = 0; i < mutations.length; i++) {
      var m = mutations[i];
      if (m.type === "characterData") translateTextNode(m.target);
      else if (m.type === "childList") {
        for (var j = 0; j < m.addedNodes.length; j++) {
          var n = m.addedNodes[j];
          if (n.nodeType === 3) translateTextNode(n);
          else if (n.nodeType === 1 && once(n)) { walk(n); translateElement(n); }
        }
      } else if (m.type === "attributes" && once(m.target)) translateElement(m.target);
    }
  });

  function start() {
    loadDict().then(function () {
      applyAll();
      mo.observe(document.body || document.documentElement, {
        childList: true, subtree: true, characterData: true,
        attributes: true, attributeFilter: ["placeholder", "title", "aria-label"],
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
    setInterval(function () { if (DICT && VALUE_DICT && Object.keys(VALUE_DICT).length) translateValues(); }, 900);
  }
})();
