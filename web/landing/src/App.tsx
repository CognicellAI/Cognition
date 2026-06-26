import { useRef } from "react";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

const surfaces = [
  {
    title: "API and streaming",
    body: "Expose agent sessions over REST and Server-Sent Events without turning every product into a runtime project.",
    image: "https://picsum.photos/seed/cognition-streams/1200/800",
  },
  {
    title: "Durable runs",
    body: "Persist sessions, messages, runs, and events so long work can be inspected instead of guessed from logs.",
    image: "https://picsum.photos/seed/cognition-durability/1200/800",
  },
  {
    title: "Scoped execution",
    body: "Carry trusted scope through the backend so tools, memory, artifacts, and events stay inside the right boundary.",
    image: "https://picsum.photos/seed/cognition-scope/1200/800",
  },
  {
    title: "Sandboxed tools",
    body: "Run agent work with execution boundaries that are visible to builders and operators.",
    image: "https://picsum.photos/seed/cognition-sandbox/1200/800",
  },
  {
    title: "Operator view",
    body: "Connect traces, metrics, events, and evaluation hooks to the work an agent actually performed.",
    image: "https://picsum.photos/seed/cognition-observe/1200/800",
  },
];

const chapters = [
  "Define agents as backend objects.",
  "Stream their work while it is happening.",
  "Persist state so sessions survive restarts.",
  "Constrain every run with builder-authorized scope.",
  "Inspect tools, traces, events, and outcomes.",
];

const routes = [
  { href: "/learn/", label: "Learn agent development" },
  { href: "/docs/", label: "Read the docs" },
  { href: "https://github.com/CognicellAI/Cognition", label: "GitHub" },
];

export function App() {
  const rootRef = useRef<HTMLElement | null>(null);
  const heroImageRef = useRef<HTMLDivElement | null>(null);
  const revealRef = useRef<HTMLParagraphElement | null>(null);
  const pinnedRef = useRef<HTMLElement | null>(null);
  const cardRefs = useRef<HTMLAnchorElement[]>([]);

  useGSAP(
    () => {
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      if (reduceMotion) {
        return;
      }

      gsap.fromTo(
        ".hero-copy > *",
        { y: 34, opacity: 0 },
        { y: 0, opacity: 1, duration: 1.1, ease: "power3.out", stagger: 0.12 },
      );

      if (heroImageRef.current) {
        gsap.fromTo(
          heroImageRef.current,
          { scale: 0.88, opacity: 0.72 },
          {
            scale: 1,
            opacity: 1,
            ease: "none",
            scrollTrigger: {
              trigger: heroImageRef.current,
              start: "top 80%",
              end: "bottom 15%",
              scrub: true,
            },
          },
        );
      }

      if (revealRef.current) {
        const words = gsap.utils.toArray<HTMLSpanElement>(".reveal-word");
        gsap.fromTo(
          words,
          { opacity: 0.14 },
          {
            opacity: 1,
            ease: "none",
            stagger: 0.08,
            scrollTrigger: {
              trigger: revealRef.current,
              start: "top 75%",
              end: "bottom 35%",
              scrub: true,
            },
          },
        );
      }

      if (pinnedRef.current) {
        ScrollTrigger.create({
          trigger: pinnedRef.current,
          start: "top top",
          end: "bottom bottom",
          pin: ".pin-panel",
          pinSpacing: false,
        });
      }

      cardRefs.current.forEach((card, index) => {
        gsap.fromTo(
          card,
          { y: 80, opacity: 0.35, scale: 0.96 },
          {
            y: 0,
            opacity: 1,
            scale: 1,
            ease: "power2.out",
            scrollTrigger: {
              trigger: card,
              start: "top 85%",
              end: "top 45%",
              scrub: true,
            },
            delay: index * 0.04,
          },
        );
      });
    },
    { scope: rootRef },
  );

  const revealText =
    "Cognition treats agent infrastructure as a backend concern. Product teams keep their workflow and policy logic, while the runtime handles sessions, streams, tools, persistence, scope, and observability.";

  return (
    <main ref={rootRef} className="page-shell">
      <nav className="site-nav" aria-label="Primary">
        <a className="brand-mark" href="/" aria-label="Cognition home">
          <img src="/assets/logo.svg" alt="" />
          <span>Cognition</span>
        </a>
        <div className="nav-links">
          <a href="/learn/">Learn</a>
          <a href="/docs/">Docs</a>
          <a href="https://github.com/CognicellAI/Cognition">GitHub</a>
        </div>
      </nav>

      <section className="hero-section">
        <div className="hero-copy">
          <p className="eyebrow">Backend infrastructure for agents</p>
          <h1>
            Define agents.{" "}
            <span
              className="inline-image"
              aria-hidden="true"
              style={{
                backgroundImage: "url(https://picsum.photos/seed/cognition-inline/500/240)",
              }}
            />
            {" "}
            Ship systems.
          </h1>
          <p className="hero-deck">
            Cognition gives production agent teams the API, streaming, persistence,
            sandboxing, scoping, and observability they would otherwise rebuild by hand.
          </p>
          <div className="hero-actions" aria-label="Primary actions">
            <a className="button button-primary" href="/learn/">
              Learn agent development
            </a>
            <a className="button button-secondary" href="/docs/">
              Read the docs
            </a>
          </div>
        </div>
        <div ref={heroImageRef} className="hero-art" aria-hidden="true">
          <div className="hero-orbit hero-orbit-a" />
          <div className="hero-orbit hero-orbit-b" />
          <img src="https://picsum.photos/seed/cognition-runtime/1400/1100" alt="" />
        </div>
      </section>

      <section className="bento-section" aria-labelledby="bento-heading">
        <div className="section-intro">
          <h2 id="bento-heading">The backend pieces agents need</h2>
          <p>
            A useful agent is more than a model call. It needs a place to run, a way
            to stream progress, memory boundaries, tool controls, and a record of what happened.
          </p>
        </div>

        <div className="bento-grid grid-flow-dense">
          {surfaces.map((surface, index) => (
            <a
              key={surface.title}
              ref={(node) => {
                if (node) {
                  cardRefs.current[index] = node;
                }
              }}
              className={`bento-card bento-card-${index + 1}`}
              href="/docs/"
            >
              <div className="card-image">
                <img src={surface.image} alt="" />
              </div>
              <div>
                <h3>{surface.title}</h3>
                <p>{surface.body}</p>
              </div>
            </a>
          ))}
        </div>
      </section>

      <section className="reveal-section" aria-labelledby="reveal-heading">
        <h2 id="reveal-heading">A backend boundary the team can reason about</h2>
        <p ref={revealRef} className="reveal-text">
          {revealText.split(" ").map((word, index) => (
            <span className="reveal-word" key={`${word}-${index}`}>
              {word}{" "}
            </span>
          ))}
        </p>
      </section>

      <section ref={pinnedRef} className="pinned-section" aria-labelledby="pinned-heading">
        <div className="pin-panel">
          <h2 id="pinned-heading">What Cognition owns</h2>
          <p>
            Your application keeps the product rules. Cognition provides the runtime
            substrate that makes agent work inspectable, durable, and scoped.
          </p>
        </div>
        <div className="chapter-stack">
          {chapters.map((chapter) => (
            <article className="chapter-card" key={chapter}>
              <span>{chapter}</span>
            </article>
          ))}
        </div>
      </section>

      <section className="accordion-section" aria-labelledby="paths-heading">
        <div className="section-intro">
          <h2 id="paths-heading">Choose the surface you need</h2>
          <p>
            The public site has separate jobs. Start with the course if you are
            learning agents. Go to the docs when you need exact Cognition behavior.
          </p>
        </div>
        <div className="route-accordion">
          {routes.map((route) => (
            <a className="route-panel" href={route.href} key={route.href}>
              <span>{route.label}</span>
            </a>
          ))}
        </div>
      </section>

      <div className="marquee" aria-hidden="true">
        <div className="marquee-track">
          <span>runtime</span>
          <span>streaming</span>
          <span>persistence</span>
          <span>scoping</span>
          <span>sandboxing</span>
          <span>observability</span>
          <span>evaluation</span>
          <span>runtime</span>
          <span>streaming</span>
          <span>persistence</span>
          <span>scoping</span>
          <span>sandboxing</span>
          <span>observability</span>
          <span>evaluation</span>
        </div>
      </div>

      <footer className="site-footer">
        <div>
          <h2>Build the agent. Keep the backend legible.</h2>
          <p>
            Start with the course, inspect the docs, or read the code. The important
            part is that the agent system has boundaries you can explain.
          </p>
        </div>
        <div className="footer-actions">
          <a className="button button-primary" href="/learn/">
            Start learning
          </a>
          <a className="button button-secondary" href="/docs/">
            Open docs
          </a>
        </div>
      </footer>
    </main>
  );
}
