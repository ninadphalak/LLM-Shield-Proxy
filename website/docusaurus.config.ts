import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// This runs in Node.js - Don't use client-side code here (browser APIs, JSX...)

const config: Config = {
  title: 'LLM-Shield-Proxy',
  tagline: 'Ultra-Low Latency Generative AI Sanitization for Highly Regulated Enterprise Infrastructure',
  favicon: 'img/favicon.svg',

  // Set the production url of your site here
  url: 'https://project-0039f5fd-ac66-4a1c-9e0.web.app',
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
          'PII redaction, LLM proxy, AI gateway, DLP for LLMs, HIPAA, SOC 2, GDPR, zero-egress, streaming SSE redaction, PHI, PCI, LLM firewall, AI security',
      },
    ],
    colorMode: {
      defaultMode: 'dark',
      disableSwitch: false,
      respectPrefersColorScheme: true,
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
          ],
        },
        {
          title: 'Project',
          items: [
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
  themes: ['@docusaurus/theme-mermaid'],
};

export default config;
