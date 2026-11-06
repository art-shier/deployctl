import {
  ArrowRight,
  Box,
  FolderGit2,
  RefreshCw,
  ShieldCheck,
  Terminal,
} from "lucide-react";
import "./landing.css";

export function LandingPage({ login }: { login: () => void }) {
  return (
    <div className="landing">
      <header className="landing-header">
        <div className="brand">
          <Terminal size={25} />
          <span>
            ctl<span className="brand-dot">.</span>
          </span>
        </div>
        <span className="landing-header-label">部署管理平台</span>
        <button className="primary" onClick={login}>
          登录
          <ArrowRight size={16} />
        </button>
      </header>
      <main className="landing-main">
        <section className="landing-hero" aria-labelledby="landing-title">
          <div className="landing-intro">
            <p className="landing-kicker">从发布到运行</p>
            <h1 id="landing-title">
              让部署回归简单。
              <br />
              <span>让每个版本都有迹可循。</span>
            </h1>
            <p className="landing-lead">
              在一个地方管理项目、镜像与环境配置，将版本部署到指定节点，持续掌握服务的运行与更新状态。
            </p>
            <div className="landing-hero-actions">
              <button className="primary" onClick={login}>
                登录管理台
                <ArrowRight size={18} />
              </button>
              <p>
                <ShieldCheck size={16} />
                凭管理员凭据访问
              </p>
            </div>
          </div>
          <aside className="landing-flow" aria-label="部署流程说明">
            <div className="landing-flow-heading">
              <Terminal size={19} />
              <span>一条清晰的交付路径</span>
              <span className="landing-flow-label">工作流程</span>
            </div>
            <ol>
              <li>
                <span className="landing-step">01</span>
                <div>
                  <strong>管理发布版本</strong>
                  <p>集中存储镜像与发布包，明确每次部署的来源。</p>
                </div>
              </li>
              <li>
                <span className="landing-step">02</span>
                <div>
                  <strong>选择项目与节点</strong>
                  <p>按项目、环境和节点授权，将版本交付到目标服务器。</p>
                </div>
              </li>
              <li>
                <span className="landing-step">03</span>
                <div>
                  <strong>查看运行结果</strong>
                  <p>跟踪部署任务、服务状态与操作记录，按需升级或回滚。</p>
                </div>
              </li>
            </ol>
            <div className="landing-flow-footer">
              统一管理配置与发布，让日常运维更有条理。
            </div>
          </aside>
        </section>
        <section
          className="landing-capabilities"
          aria-labelledby="capabilities-title"
        >
          <div className="landing-section-heading">
            <p className="landing-kicker">围绕实际部署工作</p>
            <h2 id="capabilities-title">项目、节点与版本，集中管理。</h2>
          </div>
          <div className="landing-feature-grid">
            <article>
              <FolderGit2 size={24} />
              <h3>项目与镜像管理</h3>
              <p>
                按项目组组织服务，管理发布版本、镜像与环境配置，为不同项目分配访问权限。
              </p>
            </article>
            <article>
              <Box size={24} />
              <h3>远程节点部署</h3>
              <p>
                节点主动连接中心。在管理台选择目标节点，执行安装、升级与回滚，查看任务进度。
              </p>
            </article>
            <article>
              <RefreshCw size={24} />
              <h3>中心与节点更新</h3>
              <p>
                查询官方版本清单，按需同步新版本，分别管理中心服务与节点 Agent
                的更新。
              </p>
            </article>
          </div>
        </section>
      </main>
      <footer className="landing-footer">
        <span>ctl. 部署管理平台</span>
        <span>从一次发布，到日常运行。</span>
      </footer>
    </div>
  );
}
