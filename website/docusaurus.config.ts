import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// This runs in Node.js - Don't use client-side code here (browser APIs, JSX...)

const config: Config = {
  title: 'LLM-Shield-Proxy',
  tagline: 'Keep personal data out of LLM requests: a self-hosted redacting proxy, a leak benchmark for any gateway, and two small security test tools.',
  favicon: 'img/favicon.svg',

  // The CUSTOM domain, not the Firebase project's default `.web.app` address. Docusaurus
  // builds canonical links, the sitemap and Open Graph tags from this, so leaving it as
  // the default told search engines the `.web.app` host was the real site and this one a
  // duplicate, and every shared link previewed as the project-id URL.
  url: 'https://llmshieldproxy.com',
  // Set the /<baseUrl>/ pathname under which your site is served
  // For GitHub pages deployment, it is often '/<projectName>/'
  baseUrl: '/',

  // GitHub pages deployment config.
  // If you aren't using GitHub pages, you don't need these.
  organizationName: 'ninadphalak', // Usually your GitHub org/user name.
  projectName: 'LLM-Shield-Proxy', // Usually your repo name.

  onBrokenLinks: 'warn',

  /**
   * Traffic measurement, on the same terms this project asks of everyone else.
   *
   * WHY NOT GOOGLE ANALYTICS. This site argues that you should not send personal data to a
   * third party without checking what leaves. Shipping GA on it would hand every reader's
   * browsing to an advertising company, which is the behaviour the benchmark exists to
   * catch. Cloudflare Web Analytics sets no cookie, stores no per-visitor identifier and
   * needs no consent banner, and this domain already resolves through Cloudflare, so it
   * adds no vendor that was not already in the path.
   *
   * The token is not a credential: it ships in the HTML of every page and only says which
   * site a beacon belongs to. It comes from the environment so a fork builds clean and a
   * local `npm run build` does not report traffic. Unset, this is an empty array and no
   * beacon is emitted at all.
   */
  scripts: process.env.CF_ANALYTICS_TOKEN
    ? [
        {
          src: 'https://static.cloudflareinsights.com/beacon.min.js',
          defer: true,
          'data-cf-beacon': JSON.stringify({token: process.env.CF_ANALYTICS_TOKEN}),
        },
      ]
    : [],

  // Even if you don't use internationalization, you can use this field to set
  // useful metadata like html lang. For example, if your site is Chinese, you
  // may want to replace "en" with "zh-Hans".
  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl:
            'https://github.com/ninadphalak/LLM-Shield-Proxy/tree/main/website/',
        },
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: 'img/social-card.png',
    metadata: [
      {
        name: 'keywords',
        content:
          'PII redaction proxy, LLM privacy gateway, OpenAI-compatible proxy, LLM data leak test, streaming guardrail testing, MCP security checker, SSRF, Apache 2.0',
      },
    ],
    colorMode: {
      defaultMode: 'dark',
      disableSwitch: true,
      respectPrefersColorScheme: false,
    },
    navbar: {
      title: 'LLM-Shield-Proxy',
      logo: {
        alt: 'LLM-Shield-Proxy logo',
        src: 'img/logo.svg',
      },
      // One tab per package. `data-tip` is shown as a hover box by custom.css, on mouse hover
      // and on keyboard focus. No `title`: the browser would show the same text a second time.
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'proxySidebar',
          position: 'left',
          label: 'Proxy',
          className: 'navTip',
          'data-tip': 'Self-hosted proxy that hides personal data from your LLM provider and restores it in the reply',
        },
        {
          type: 'docSidebar',
          sidebarId: 'benchmarkSidebar',
          position: 'left',
          label: 'Leak Benchmark',
          className: 'navTip',
          'data-tip': 'Test whether any LLM gateway sends personal data to the provider, in about a minute',
        },
        {
          type: 'docSidebar',
          sidebarId: 'mcpCheckSidebar',
          position: 'left',
          label: 'MCP Check',
          className: 'navTip',
          'data-tip': 'Check your own MCP server for SSRF and missing Host and Origin checks',
        },
        {
          type: 'docSidebar',
          sidebarId: 'chunkInvarianceSidebar',
          position: 'left',
          label: 'Chunk Invariance',
          className: 'navTip',
          'data-tip': 'A one-line test that catches values split across stream chunks',
        },
        {
          href: 'https://github.com/ninadphalak/LLM-Shield-Proxy',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Packages',
          items: [
            {label: 'LLM-Shield-Proxy', to: '/docs/proxy'},
            {label: 'Leak Benchmark', to: '/docs/conformance'},
            {label: 'Published results', to: '/docs/conformance/results'},
            {label: 'mcp-ssrf-check', to: '/docs/mcp-ssrf-check'},
            {label: 'chunk-invariance', to: '/docs/chunk-invariance'},
            {label: 'Browser playground', to: '/playground'},
          ],
        },
        {
          title: 'Learn',
          items: [
            {label: 'Architecture', to: '/docs/architecture'},
            {label: 'Security', to: '/docs/security'},
            {label: 'Limitations', to: '/docs/limitations'},
            {label: 'Compliance', to: '/docs/compliance-overview'},
            {label: 'Research', to: '/docs/research-publications'},
          ],
        },
        {
          title: 'Community',
          items: [
            {
              label: 'GitHub Discussions',
              href: 'https://github.com/ninadphalak/LLM-Shield-Proxy/discussions',
            },
            {
              label: 'Issues & Feature Requests',
              href: 'https://github.com/ninadphalak/LLM-Shield-Proxy/issues',
            },
            {
              label: 'Contributing Guide',
              href: 'https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/CONTRIBUTING.md',
            },
            {
              label: '30-Day Design Partner Pilot',
              to: '/docs/design-partner-pilot',
            },
          ],
        },
        {
          title: 'Project',
          items: [
            {
              label: 'Glossary',
              to: '/docs/glossary',
            },
            {
              label: 'License (Apache 2.0)',
              href: 'https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/LICENSE',
            },
            {
              label: 'Releases',
              href: 'https://github.com/ninadphalak/LLM-Shield-Proxy/releases',
            },
            {
              label: 'PyPI Package',
              href: 'https://pypi.org/project/llm-shield-proxy/',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Ninad Phalak. Licensed under Apache 2.0.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
    },
  } satisfies Preset.ThemeConfig,
  markdown: {
    mermaid: true,
  },
  themes: [
    '@docusaurus/theme-mermaid',
    // LOCAL search, deliberately, rather than hosted DocSearch.
    //
    // Docusaurus ships no search at all by default, and the usual answer is Algolia.
    // That would mean every visitor's browser calling a third party on each keystroke,
    // on the documentation site for a gateway whose selling point is that it makes no
    // outbound calls. This builds the index at build time and serves it as static
    // assets from this origin: no account, no crawler, nothing to phone home, and it
    // works behind an air gap like the rest of the product it documents.
    [
      require.resolve('@easyops-cn/docusaurus-search-local'),
      {
        hashed: true, // cache-bust the index when content changes
        indexBlog: true,
        docsRouteBasePath: '/docs',
        highlightSearchTermsOnTargetPage: true,
        explicitSearchResultPath: true,
      },
    ],
  ],
};

export default config;
