/* Enterprise H5 proof exchange. All identity and permission checks stay server-side. */
(() => {
  "use strict";
  let settings = {}, sdkPromise = null;
  const inside = () => !!window.dd?.env?.platform && window.dd.env.platform !== "notInDingTalk" || /DingTalk/i.test(navigator.userAgent);
  async function sdk() {
    if (window.dd) return window.dd;
    if (!sdkPromise) sdkPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      const timer = setTimeout(() => reject(new Error("钉钉组件加载超时，请重新尝试。")), 15000);
      script.src = "https://g.alicdn.com/dingding/dingtalk-jsapi/3.1.0/dingtalk.open.js";
      script.referrerPolicy = "no-referrer";
      script.onload = () => { clearTimeout(timer); window.dd ? resolve(window.dd) : reject(new Error("钉钉组件未就绪。")); };
      script.onerror = () => { clearTimeout(timer); reject(new Error("钉钉组件加载失败，请检查连接后重试。")); };
      document.head.append(script);
    }).catch(error => { sdkPromise = null; throw error; });
    return sdkPromise;
  }
  async function proof() {
    if (!inside()) throw new Error("请在公司钉钉工作台中打开企业知识库。");
    const dd = await sdk();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("钉钉身份验证超时，请重试。")), 20000);
      dd.ready(() => dd.runtime.permission.requestAuthCode({corpId: settings.corp_id,
        onSuccess: value => { clearTimeout(timer); value?.code ? resolve(value.code) : reject(new Error("钉钉未返回登录凭证。")); },
        onFail: () => { clearTimeout(timer); reject(new Error("钉钉未能确认身份，请确认当前企业和应用权限。")); }
      }));
    });
  }
  window.agicoDingTalk = {
    configure: value => { settings = value; }, inside,
    async login(request, bind = false) {
      if (!inside()) throw new Error("请在公司钉钉工作台中打开企业知识库。");
      const challenge = await request("/v1/auth/dingtalk/challenge", {method: "POST"});
      const code = await proof();
      return request(bind ? "/v1/auth/dingtalk/bind" : "/v1/auth/dingtalk/login", {
        method: "POST", body: {code, challenge: challenge.challenge}
      });
    }
  };
})();
