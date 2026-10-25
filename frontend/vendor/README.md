# 本地Markdown渲染依赖

marked 18.1.1解析Markdown，DOMPurify 3.4.17过滤生成HTML；固定版本来自官方npm，下载时校验包SHA512。来源、完整性及本地文件SHA256见manifest.json，许可证随文件保留。

运行时直接加载同源静态文件，不请求CDN，不需要Node或额外Python依赖。渲染入口为../markdown.js，只允许有限的排版标签和属性；脚本、事件属性、表单、图片及不安全链接不会作为可执行内容插入页面。

官方文档：[Marked](https://marked.js.org/)、[DOMPurify](https://github.com/cure53/DOMPurify)。更新时重新校验版本和渲染/安全回归，不直接改vendor源码。
