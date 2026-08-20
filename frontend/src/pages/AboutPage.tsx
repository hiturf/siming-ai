import {
  ArrowRightOutlined,
  BugOutlined,
  DatabaseOutlined,
  GithubOutlined,
  HistoryOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { Typography } from 'antd'
import AppVersion from '../components/AppVersion'
import PageWrapper from '../components/PageWrapper'
import SystemNav from '../components/SystemNav'
import './AboutPage.css'

const { Text } = Typography

const principles = [
  {
    number: '01',
    title: '作品属于作者',
    description: '数据库、文件镜像与版本快照默认保存在你选择的本机目录，作品不会被锁进一段不可迁移的聊天记录。',
  },
  {
    number: '02',
    title: '事实先于生成',
    description: '大纲、角色状态、世界规则与叙事账本共同约束写作，让模型先读懂故事已经发生了什么。',
  },
  {
    number: '03',
    title: '边界必须透明',
    description: '使用哪个模型、发送哪些上下文、任务运行到哪一步，都应当让作者看得见、能暂停、可回退。',
  },
]

const officialLinks = [
  {
    label: '源码与文档',
    detail: 'GitHub Repository',
    href: 'https://github.com/teangtang1122/siming-ai',
    icon: <GithubOutlined aria-hidden="true" />,
  },
  {
    label: '版本记录',
    detail: 'Releases & Changelog',
    href: 'https://github.com/teangtang1122/siming-ai/releases',
    icon: <HistoryOutlined aria-hidden="true" />,
  },
  {
    label: '反馈问题',
    detail: 'Issues & Suggestions',
    href: 'https://github.com/teangtang1122/siming-ai/issues/new/choose',
    icon: <BugOutlined aria-hidden="true" />,
  },
]

export default function AboutPage() {
  const year = new Date().getFullYear()

  return (
    <PageWrapper maxWidth={1180} className="about-page">
      <SystemNav current="about" />

      <article className="about-sheet">
        <header className="about-hero">
          <div className="about-seal" aria-hidden="true">
            <span>司</span>
            <span>命</span>
          </div>

          <div className="about-hero-copy">
            <span className="about-kicker">ABOUT SIMING · 关于我们</span>
            <h1>
              让长篇故事，
              <em>记得自己走过的路。</em>
            </h1>
            <p>
              司命是一款免费、开源、本地优先的 AI 长篇创作工作台。
              它把正文、设定、人物变化与创作过程放回同一张书桌，让 AI 服务于作者的判断，而不是替代作者。
            </p>
            <div className="about-hero-actions" aria-label="官方链接">
              <a
                className="about-primary-link"
                href="https://github.com/teangtang1122/siming-ai"
                target="_blank"
                rel="noreferrer"
              >
                <GithubOutlined aria-hidden="true" />
                查看开源项目
                <ArrowRightOutlined aria-hidden="true" />
              </a>
              <a
                className="about-secondary-link"
                href="https://github.com/teangtang1122/siming-ai/releases"
                target="_blank"
                rel="noreferrer"
              >
                获取最新版本
              </a>
            </div>
          </div>

          <dl className="about-colophon" aria-label="产品信息">
            <div>
              <dt>当前版本</dt>
              <dd>
                <AppVersion className="about-version" />
                <small>由运行中的司命读取</small>
              </dd>
            </div>
            <div>
              <dt>开源许可</dt>
              <dd>Apache 2.0</dd>
            </div>
            <div>
              <dt>发起与维护</dt>
              <dd>teangtang1122</dd>
            </div>
            <div>
              <dt>产品形态</dt>
              <dd>Windows · Android · Gateway</dd>
            </div>
          </dl>
        </header>

        <section className="about-manifesto" aria-labelledby="about-manifesto-title">
          <div className="about-section-index" aria-hidden="true">
            <span>卷首</span>
            <strong>序</strong>
          </div>
          <div>
            <span className="about-kicker">WHY WE BUILD</span>
            <h2 id="about-manifesto-title">写长篇，难的从来不只是写出下一段。</h2>
            <p>
              真正困难的是让几百章之后的人物仍然记得承诺，让伏笔有来处也有归宿，让世界规则不会因为一次生成而改写。
              司命因此从“保存故事事实”出发，再把合适的上下文交给模型——作者始终拥有最后一笔。
            </p>
          </div>
        </section>

        <section className="about-principles" aria-labelledby="about-principles-title">
          <div className="about-section-heading">
            <span className="about-kicker">OUR PRINCIPLES</span>
            <h2 id="about-principles-title">三件不愿妥协的事</h2>
          </div>
          <div className="about-principle-grid">
            {principles.map((principle) => (
              <article className="about-principle" key={principle.number}>
                <span>{principle.number}</span>
                <h3>{principle.title}</h3>
                <p>{principle.description}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="about-boundary" aria-labelledby="about-boundary-title">
          <div className="about-boundary-intro">
            <span className="about-kicker">DATA BOUNDARY</span>
            <h2 id="about-boundary-title">你的作品，默认留在你选择的位置。</h2>
            <p>
              司命没有官方小说数据云服务。只有当你主动使用云端 API 或需要联网的 CLI 时，当前任务选中的提示词、正文片段和上下文才会发送给相应模型提供方。
            </p>
          </div>
          <ol className="about-data-flow" aria-label="数据处理边界">
            <li>
              <DatabaseOutlined aria-hidden="true" />
              <div><strong>本机作品库</strong><span>数据库、镜像与快照</span></div>
            </li>
            <li>
              <SafetyCertificateOutlined aria-hidden="true" />
              <div><strong>按任务筛选</strong><span>只组合本次所需上下文</span></div>
            </li>
            <li>
              <ArrowRightOutlined aria-hidden="true" />
              <div><strong>你选择的模型</strong><span>数据政策由提供方决定</span></div>
            </li>
          </ol>
        </section>

        <section className="about-community" aria-labelledby="about-community-title">
          <div className="about-community-copy">
            <span className="about-kicker">OPEN SOURCE & COMMUNITY</span>
            <h2 id="about-community-title">一张持续展开的共同书桌</h2>
            <p>
              司命由 teangtang1122 发起并维护，也因每一位试用者、作者和开源贡献者的反馈而继续生长。
              软件本身永久免费，欢迎提交代码、文档、可复现问题与真实创作体验。
            </p>
            <p className="about-contact">
              用户交流 QQ 群
              <Text copyable={{ text: '814283606', tooltips: ['复制群号', '已复制'] }}>814283606</Text>
            </p>
          </div>
          <nav className="about-official-links" aria-label="项目资源">
            {officialLinks.map((link) => (
              <a key={link.href} href={link.href} target="_blank" rel="noreferrer">
                {link.icon}
                <span><strong>{link.label}</strong><small>{link.detail}</small></span>
                <ArrowRightOutlined className="about-link-arrow" aria-hidden="true" />
              </a>
            ))}
          </nav>
        </section>

        <footer className="about-footer">
          <span className="about-footer-mark" aria-hidden="true">命</span>
          <p>
            © {year} teangtang1122 · Licensed under Apache 2.0
            <small>愿每一个故事，都能抵达它应有的结局。</small>
          </p>
        </footer>
      </article>
    </PageWrapper>
  )
}
