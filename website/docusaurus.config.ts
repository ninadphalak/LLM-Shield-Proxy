import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// This runs in Node.js - Don't use client-side code here (browser APIs, JSX...)

const config: Config = {
  title: 'LLM-Shield-Proxy',
  tagline: 'Open, reproducible streaming privacy and audit evidence for enterprise LLM infrastructure',
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
          'streaming privacy gateway, PII redaction, LLM proxy, AI gateway, open conformance specification, audit evidence, OSCAL 1.2, MCP governance, SSE rehydration, Apache 2.0',
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
        alt: 'LLM-Shield-Proxy Logo',
        src: 'img/logo.svg',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'tutorialSidebar',
          position: 'left',
          label: 'Documentation',
        },
        {
          to: '/docs/conformance',
          label: 'Conformance Lab',
          position: 'left',
        },
        {
          to: '/docs/research-publications',
          label: 'Research',
          position: 'left',
        },
        {
          to: '/docs/design-partner-pilot',
          label: 'Pilot Program',
          position: 'left',
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
          title: 'Docs',
          items: [
            {
              label: 'Conformance Specification',
              to: '/docs/conformance/specification-v1',
            },
            {
              label: 'Published Results',
              to: '/docs/conformance/results',
            },
            {
              label: 'Architecture',
              to: '/docs/architecture',
            },
            {
              label: 'Security',
              to: '/docs/security',
            },
            {
              label: 'Compliance',
              to: '/docs/compliance-overview',
            },
            {
              label: 'Evidence Plane Status',
              to: '/docs/evidence-plane-status',
            },
            {
              label: 'Deployment',
              to: '/docs/deployment',
            },
            {
              label: 'Features Overview',
              to: '/docs/features-overview',
            },
            {
              label: 'Policies',
              to: '/docs/policies',
            },
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
