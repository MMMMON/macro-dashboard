'use strict';

(() => {
  const runtime = '/liquidity-runtime.js?v=20261016-1';
  let loading = false;

  function notice(message, retry = false) {
    const node = document.getElementById('pqg-error');
    if (!node) return;
    node.hidden = false;
    node.replaceChildren(document.createTextNode(message));
    if (retry) {
      const button = document.createElement('button');
      button.className = 'dashboard-action';
      button.id = 'retry-dashboard';
      button.type = 'button';
      button.textContent = '重新加载看板';
      button.addEventListener('click', () => load(true));
      node.append(document.createTextNode(' '), button);
    }
  }

  async function load(manual = false) {
    if (loading || window.__LIQUIDITY_RUNTIME_READY__) return;
    loading = true;
    if (manual) notice('正在重新加载图表与数据…');
    for (let attempt = 0; attempt < 3 && !window.__LIQUIDITY_RUNTIME_READY__; attempt += 1) {
      await new Promise(resolve => {
        const script = document.createElement('script');
        script.src = `${runtime}&attempt=${attempt}&ts=${Date.now()}`;
        script.async = true;
        const finish = () => {
          clearTimeout(timer);
          script.onload = script.onerror = null;
          if (!window.__LIQUIDITY_RUNTIME_READY__) script.remove();
          resolve();
        };
        const timer = setTimeout(finish, 15000);
        script.onload = script.onerror = finish;
        document.head.append(script);
      });
    }
    loading = false;
    if (!window.__LIQUIDITY_RUNTIME_READY__) {
      notice('看板程序加载失败。请检查浏览器扩展或网络拦截后重试；这不是数据为空。', true);
      const status = document.getElementById('pqg-update-status');
      if (status) status.textContent = '页面程序未加载';
    }
  }

  load();
})();
