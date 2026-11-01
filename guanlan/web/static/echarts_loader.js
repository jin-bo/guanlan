"use strict";
// ECharts 运行时的公共懒加载（P4.27，决策P4.27-4）。原在 flint_enhance.js（P4.20），图谱面板也要用，
// 抽出来让两处共用**一个**单例 Promise——同页无论先开哪个，`vendor/echarts.min.js` 只注入一次。
// 行为与 P4.20 原实现逐字一致：注入 <script> 读 window.echarts（同 mermaid 姿态）；失败不缓存
// Promise、并移除失败的 <script>，让下次调用从干净状态重试。须先于 flint_enhance.js 载入。
let _echartsPromise = null;
function loadECharts() {
  if (_echartsPromise) return _echartsPromise;
  _echartsPromise = new Promise((resolve, reject) => {
    if (window.echarts) return resolve(window.echarts); // 容错：已被别处加载
    const s = document.createElement("script");
    s.src = "/static/vendor/echarts.min.js";
    s.onload = () => (window.echarts ? resolve(window.echarts) : (s.remove(), reject(new Error("no echarts"))));
    // 失败即移除本 <script>：配合失败不缓存 promise，让重试从干净状态重来、不在 <head> 堆死标签。
    s.onerror = () => { s.remove(); reject(new Error("echarts 运行时加载失败")); };
    document.head.appendChild(s);
  }).catch((e) => { _echartsPromise = null; throw e; });
  return _echartsPromise;
}
