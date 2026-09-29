// CodeOrbit frontend — talks to the Flask backend's /api/analyze and
// /api/explain endpoints and renders the result. No hardcoded graph data
// lives here; everything comes from the real repository analysis.

// Render backend used by the GitHub Pages frontend.
const API_BASE_URL = "https://codeorbit-backend-0t15.onrender.com";

const state = {
  analysis: null,
  selectedNode: null,
  mode: "architecture",
  theme: "dark",
};

const ICONS = [
  { match: /cli/i, icon: "terminal-square" },
  { match: /config|settings/i, icon: "settings-2" },
  { match: /agent|orchestrat/i, icon: "box" },
  { match: /provider|client/i, icon: "cog" },
  { match: /registry|store|db|database/i, icon: "database" },
  { match: /planner|plan/i, icon: "git-fork" },
  { match: /calculator|math/i, icon: "calculator" },
  { match: /image|photo|convert/i, icon: "image" },
  { match: /reader|read|writer/i, icon: "file-text" },
  { match: /route|api|server/i, icon: "server" },
  { match: /test/i, icon: "flask-conical" },
];

function iconFor(name) {
  const hit = ICONS.find(x => x.match.test(name));
  return hit ? hit.icon : "file-code";
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);

  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") {
      node.className = v;
    } else if (k === "text") {
      node.textContent = v;
    } else {
      node.setAttribute(k, v);
    }
  }

  children.forEach(c => node.appendChild(c));
  return node;
}


// ---------- bootstrapping ----------

document.addEventListener("DOMContentLoaded", () => {
  if (window.lucide) {
    lucide.createIcons();
  }

  initTheme();

  const uploadZipBtn = document.getElementById("uploadZipBtn");
  const zipInput = document.getElementById("zipInput");
  const sampleRepoBtn = document.getElementById("sampleRepoBtn");
  const githubRepoBtn = document.getElementById("githubRepoBtn");
  const newAnalysisBtn = document.getElementById("newAnalysisBtn");
  const themeToggleBtn = document.getElementById("themeToggleBtn");

  if (uploadZipBtn && zipInput) {
    uploadZipBtn.addEventListener("click", () => {
      zipInput.click();
    });
  }

  if (zipInput) {
    zipInput.addEventListener("change", onZipChosen);
  }

  if (sampleRepoBtn) {
    sampleRepoBtn.addEventListener("click", () => analyze({ sample: true }));
  }

  if (githubRepoBtn) {
    githubRepoBtn.addEventListener("click", () => {
      const url = window.prompt(
        "Paste a public GitHub repository URL:",
        "https://github.com/"
      );

      if (url === null) return;

      const githubUrl = url.trim();

      if (!githubUrl) {
        flashStatus("Please enter a GitHub repository URL.");
        return;
      }

      analyze({ githubUrl });
    });
  }

  if (newAnalysisBtn) {
    newAnalysisBtn.addEventListener("click", resetApp);
  }

  if (themeToggleBtn) {
    themeToggleBtn.addEventListener("click", toggleTheme);
  }

  document.querySelectorAll(".tabs .tab, .nav-item").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      handleNav(btn.dataset.nav);
    });
  });

  document.querySelectorAll(".graph-modes .mode-btn").forEach(btn => {
    btn.addEventListener("click", () => setMode(btn.dataset.mode));
  });

  const viewAsSelect = document.getElementById("viewAsSelect");

  if (viewAsSelect) {
    viewAsSelect.addEventListener("change", (e) => {
      setMode(e.target.value);
    });
  }

  document.querySelectorAll(".detail-tabs .dtab").forEach(btn => {
    btn.addEventListener("click", () => {
      setActiveGroup(".detail-tabs .dtab", btn);

      document.querySelectorAll(".dpane").forEach(p => {
        p.hidden = p.dataset.pane !== btn.dataset.dtab;
      });
    });
  });

  const explainBtn = document.getElementById("explainBtn");
  const copyMermaidBtn = document.getElementById("copyMermaidBtn");
  const exportBtn = document.getElementById("exportBtn");
  const detailCloseBtn = document.getElementById("detailCloseBtn");

  if (explainBtn) {
    explainBtn.addEventListener("click", explainSelected);
  }

  if (copyMermaidBtn) {
    copyMermaidBtn.addEventListener("click", copyMermaid);
  }

  if (exportBtn) {
    exportBtn.addEventListener("click", exportJson);
  }

  if (detailCloseBtn) {
    detailCloseBtn.addEventListener("click", () => {
      document.getElementById("detailPanel").hidden = true;
    });
  }

  const zoomInBtn = document.getElementById("zoomInBtn");
  const zoomOutBtn = document.getElementById("zoomOutBtn");
  const zoomResetBtn = document.getElementById("zoomResetBtn");
  const fullscreenBtn = document.getElementById("fullscreenBtn");

  if (zoomInBtn) {
    zoomInBtn.addEventListener("click", () => zoom(0.1));
  }

  if (zoomOutBtn) {
    zoomOutBtn.addEventListener("click", () => zoom(-0.1));
  }

  if (zoomResetBtn) {
    zoomResetBtn.addEventListener("click", () => setZoom(1));
  }

  if (fullscreenBtn) {
    fullscreenBtn.addEventListener("click", toggleFullscreen);
  }

  document.addEventListener("fullscreenchange", syncFullscreenIcon);
  document.addEventListener("webkitfullscreenchange", syncFullscreenIcon);

  const modalCloseBtn = document.getElementById("modalCloseBtn");
  const modalOverlay = document.getElementById("modalOverlay");

  if (modalCloseBtn) {
    modalCloseBtn.addEventListener("click", closeModal);
  }

  if (modalOverlay) {
    modalOverlay.addEventListener("click", (e) => {
      if (e.target.id === "modalOverlay") {
        closeModal();
      }
    });
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeModal();
    }
  });
});


function setActiveGroup(selector, activeEl) {
  if (!activeEl) return;

  document.querySelectorAll(selector).forEach(i => {
    i.classList.remove("active");
  });

  activeEl.classList.add("active");
}


function onZipChosen(e) {
  const file = e.target.files[0];

  if (file) {
    analyze({ file });
  }
}


// ---------- theme ----------

function initTheme() {
  const saved = localStorage.getItem("codeorbit-theme");

  const theme =
    saved ||
    (
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: light)").matches
        ? "light"
        : "dark"
    );

  applyTheme(theme);
}


function toggleTheme() {
  applyTheme(state.theme === "light" ? "dark" : "light");
}


function applyTheme(theme) {
  state.theme = theme;

  document.documentElement.setAttribute("data-theme", theme);

  localStorage.setItem("codeorbit-theme", theme);

  const icon = document.querySelector("#themeToggleBtn i");

  if (icon) {
    icon.setAttribute(
      "data-lucide",
      theme === "light" ? "moon" : "sun"
    );

    if (window.lucide) {
      lucide.createIcons();
    }
  }

  if (window.mermaid) {
    mermaid.initialize({
      startOnLoad: false,
      theme: theme === "light" ? "default" : "dark"
    });

    if (state.mode === "flow" && state.analysis) {
      renderMode();
    }
  }
}


// ---------- top-level navigation ----------

function handleNav(target) {
  document
    .querySelectorAll(
      `.tabs .tab[data-nav="${target}"], .nav-item[data-nav="${target}"]`
    )
    .forEach(b => {
      setActiveGroup(
        b.classList.contains("tab")
          ? ".tabs .tab"
          : ".nav-item",
        b
      );
    });

  switch (target) {
    case "analyze":
      window.scrollTo({
        top: 0,
        behavior: "smooth"
      });
      break;

    case "architecture":
      setMode("architecture");
      scrollToId("workspace");
      break;

    case "explain":
      if (!state.analysis) {
        flashStatus("Analyze a repository first.");
        break;
      }

      scrollToId("workspace");

      const detailPanel = document.getElementById("detailPanel");

      if (detailPanel) {
        detailPanel.hidden = false;
      }

      const overviewTab = document.querySelector(
        '.dtab[data-dtab="overview"]'
      );

      if (overviewTab) {
        overviewTab.click();
      }

      break;

    case "insights":
      if (!state.analysis) {
        flashStatus("Analyze a repository first.");
        break;
      }

      scrollToId("bottomGrid");
      break;

    case "explorer":
      openFileExplorer();
      break;

    case "docs":
      openDocs();
      break;
  }
}


function scrollToId(id) {
  const node = document.getElementById(id);

  if (node && !node.hidden) {
    node.scrollIntoView({
      behavior: "smooth",
      block: "start"
    });
  }
}


function flashStatus(msg) {
  const status = document.getElementById("uploadStatus");

  if (!status) return;

  status.textContent = msg;

  window.scrollTo({
    top: 0,
    behavior: "smooth"
  });

  setTimeout(() => {
    if (status.textContent === msg) {
      status.textContent = "";
    }
  }, 2500);
}


// ---------- modal ----------

function openModal(title, bodyNode) {
  const titleElement = document.getElementById("modalTitle");
  const body = document.getElementById("modalBody");
  const overlay = document.getElementById("modalOverlay");

  if (!titleElement || !body || !overlay) return;

  titleElement.textContent = title;

  body.innerHTML = "";
  body.appendChild(bodyNode);

  overlay.hidden = false;

  if (window.lucide) {
    lucide.createIcons();
  }
}


function closeModal() {
  const overlay = document.getElementById("modalOverlay");

  if (overlay) {
    overlay.hidden = true;
  }
}


// ---------- File Explorer ----------

function openFileExplorer() {
  if (!state.analysis) {
    const p = el(
      "p",
      {
        text: "Analyze a repository first, then this shows its real file tree."
      }
    );

    openModal("File Explorer", p);
    return;
  }

  const root = {};

  state.analysis.nodes.forEach(n => {
    const parts = n.id.split("/");
    let cur = root;

    parts.forEach((part, i) => {
      const isFile = i === parts.length - 1;

      cur.children = cur.children || {};

      if (!cur.children[part]) {
        cur.children[part] = {
          name: part,
          isFile,
          id: isFile ? n.id : null
        };
      }

      cur = cur.children[part];
    });
  });


  function renderTree(node) {
    const wrap = el("div", {
      class: "tree"
    });

    if (!node.children) {
      return wrap;
    }

    Object.values(node.children)
      .sort((a, b) =>
        a.isFile === b.isFile
          ? a.name.localeCompare(b.name)
          : a.isFile
            ? 1
            : -1
      )
      .forEach(child => {
        if (child.isFile) {
          const row = el("div", {
            class: "tree-node"
          });

          row.appendChild(
            el("i", {
              "data-lucide": "file-code"
            })
          );

          row.appendChild(
            el("span", {
              text: child.name
            })
          );

          row.addEventListener("click", () => {
            closeModal();

            setMode("architecture");
            scrollToId("workspace");

            const detailPanel =
              document.getElementById("detailPanel");

            if (detailPanel) {
              detailPanel.hidden = false;
            }

            selectNode(child.id);
          });

          wrap.appendChild(row);

        } else {
          const row = el("div", {
            class: "tree-node folder"
          });

          row.appendChild(
            el("i", {
              "data-lucide": "folder"
            })
          );

          row.appendChild(
            el("span", {
              text: child.name + "/"
            })
          );

          wrap.appendChild(row);

          const childTree = renderTree(child);

          childTree.classList.add("tree-children");

          wrap.appendChild(childTree);
        }
      });

    return wrap;
  }


  openModal(
    `File Explorer: ${state.analysis.projectName}`,
    renderTree(root)
  );
}


// ---------- Documentation ----------

function openDocs() {
  const body = el("div");

  body.innerHTML = `
    <h4>Overview</h4>

    <p>
      CodeOrbit is a repository analysis tool that transforms source code
      into an interactive view of a project's structure, dependencies,
      modules, and architecture.
    </p>

    <h4>Getting Started</h4>

    <ol>
      <li>
        Choose <strong>Upload ZIP</strong> to analyze a local repository.
      </li>

      <li>
        Choose <strong>GitHub URL</strong> to analyze a public GitHub
        repository directly.
      </li>

      <li>
        Choose <strong>Try Sample Repo</strong> to explore CodeOrbit
        using the included demonstration project.
      </li>
    </ol>

    <h4>Repository Analysis</h4>

    <p>
      After analysis, CodeOrbit identifies source files, modules,
      imports, dependencies, project structure, and other relevant
      relationships within the repository.
    </p>

    <p>
      The results are presented through an interactive architecture graph,
      project statistics, technology information, insights, and detailed
      file information.
    </p>

    <h4>Architecture Views</h4>

    <ul>
      <li>
        <strong>Architecture Graph</strong>
        provides an interactive visual representation of the repository.
      </li>

      <li>
        <strong>Dependency Graph</strong>
        focuses on relationships between different modules.
      </li>

      <li>
        <strong>Flow (Mermaid)</strong>
        displays the generated Mermaid diagram and its underlying
        diagram definition.
      </li>
    </ul>

    <h4>File Details</h4>

    <p>
      Select any file in the architecture graph to inspect its details.
      The information panel can include the file purpose, source preview,
      dependencies, and files that depend on it.
    </p>

    <h4>AI Explanation</h4>

    <p>
      <strong>Explain with AI</strong> uses AI to generate a
      plain-language explanation of the selected file, including
      its architectural role, purpose, dependencies, and how it
      fits into the overall project.
    </p>

    <p>
      CodeOrbit uses Gemini for AI-powered explanations in the
      deployed application. When Gemini is unavailable, CodeOrbit
      can fall back to a local Ollama instance and then use a
      code-based explanation as a final fallback.
    </p>

    <h4>File Explorer</h4>

    <p>
      Use <strong>File Explorer</strong> to browse the analyzed repository
      structure. Selecting a source file from the explorer opens its
      corresponding analysis details.
    </p>

    <h4>Insights & Statistics</h4>

    <p>
      CodeOrbit provides project-level information including the number
      of files, modules, dependencies, detected technologies, and
      generated repository insights.
    </p>

    <h4>Export</h4>

    <p>
      Use <strong>Export</strong> to save the complete analysis result
      as a JSON file for further inspection, processing, or integration
      with other tools.
    </p>

    <h4>Supported Projects</h4>

    <p>
      CodeOrbit is designed to analyze supported source repositories
      and automatically determine the project's structure and available
      technologies from the uploaded or connected source code.
    </p>

    <h4>Important Notes</h4>

    <ul>
      <li>
        GitHub analysis currently requires a publicly accessible
        repository.
      </li>

      <li>
        Analysis results are generated from the source code available
        in the selected repository.
      </li>

      <li>
        Import and dependency relationships are resolved from the
        project's source structure and may not represent a complete
        language-level type analysis.
      </li>
    </ul>
  `;

  openModal("CodeOrbit Documentation", body);
}


// ---------- API ----------

async function analyze({ file, sample, githubUrl }) {
  const status = document.getElementById("uploadStatus");

  if (!status) return;

  status.textContent = "Analyzing repository…";

  try {
    let res;

    if (file) {
      const form = new FormData();

      form.append("repo", file);

      res = await fetch(`${API_BASE_URL}/api/analyze`, {
        method: "POST",
        body: form
      });

    } else if (githubUrl) {
      res = await fetch(`${API_BASE_URL}/api/analyze`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          github_url: githubUrl
        })
      });

    } else {
      res = await fetch(`${API_BASE_URL}/api/analyze`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          sample: true
        })
      });
    }

    const data = await res.json();

    if (!res.ok) {
      throw new Error(
        data.error || "Analysis failed"
      );
    }

    if (!data.nodes || data.nodes.length === 0) {
      status.textContent =
        "No source files were found in that repository.";

      return;
    }

    status.textContent = "";

    state.analysis = data;

    render(data);

  } catch (err) {
    console.error("CodeOrbit analysis error:", err);

    status.textContent =
      err.message || "Analysis failed.";
  }
}


// ---------- rendering ----------

function render(data) {
  document.getElementById("emptyState").hidden = true;
  document.getElementById("projectHeader").hidden = false;
  document.getElementById("workspace").hidden = false;
  document.getElementById("bottomGrid").hidden = false;
  document.getElementById("detailPanel").hidden = false;

  document.getElementById("projectName").textContent =
    data.projectName || "repository";

  document.getElementById("metaLang").textContent =
    data.language;

  document.getElementById("metaFiles").textContent =
    data.stats.files;

  document.getElementById("metaModules").textContent =
    data.stats.modules;

  document.getElementById("metaDeps").textContent =
    data.stats.dependencies;


  document.getElementById("statFiles").textContent =
    data.stats.files;

  document.getElementById("statModules").textContent =
    data.stats.modules;

  document.getElementById("statDeps").textContent =
    data.stats.dependencies;

  document.getElementById("statTools").textContent =
    data.stats.tools;


  const insightList =
    document.getElementById("insightList");

  insightList.innerHTML = "";

  const insightIcon = {
    ok: "check-circle-2",
    warn: "alert-triangle",
    info: "info"
  };

  data.insights.forEach(ins => {
    const li = el("li", {
      class: ins.type
    });

    li.appendChild(
      el("i", {
        "data-lucide":
          insightIcon[ins.type] || "info"
      })
    );

    li.appendChild(
      document.createTextNode(ins.text)
    );

    insightList.appendChild(li);
  });


  const techGrid =
    document.getElementById("techGrid");

  techGrid.innerHTML = "";

  data.stack.forEach(name => {
    const pill = el("span", {
      class: "tech-pill"
    });

    pill.appendChild(
      el("span", {
        class: "dot",
        text: "●"
      })
    );

    pill.appendChild(
      document.createTextNode(name)
    );

    techGrid.appendChild(pill);
  });


  document.getElementById("mermaidCode").textContent =
    data.mermaid;

  setMode("architecture", {
    skipScroll: true
  });

  selectNode(data.entry);

  if (window.lucide) {
    lucide.createIcons();
  }
}


function renderGraph(data) {
  const transform =
    document.getElementById("graphTransform");

  const svg =
    document.getElementById("connectors");

  const layer =
    document.getElementById("nodeLayer");

  if (!transform || !svg || !layer) {
    return;
  }

  layer.innerHTML = "";
  svg.innerHTML = "";

  const COL_W = 230;
  const NODE_W = 190;
  const ROW_H = 96;
  const PAD = 30;

  const crossOnly =
    state.mode === "dependency";

  const nodesById = {};

  data.nodes.forEach(n => {
    nodesById[n.id] = n;
  });


  const edgesToDraw = crossOnly
    ? data.edges.filter(
        e =>
          nodesById[e.source] &&
          nodesById[e.target] &&
          nodesById[e.source].group !==
            nodesById[e.target].group
      )
    : data.edges;


  const dependencyNote =
    document.getElementById("dependencyNote");

  if (dependencyNote) {
    dependencyNote.hidden = !crossOnly;
  }


  const byLevel = {};

  data.nodes.forEach(n => {
    byLevel[n.level] =
      byLevel[n.level] || [];

    byLevel[n.level].push(n);
  });


  const levels = Object.keys(byLevel)
    .map(Number)
    .sort((a, b) => a - b);


  if (!levels.length) {
    return;
  }


  const maxRows = Math.max(
    ...levels.map(
      l => byLevel[l].length
    )
  );


  const width =
    levels.length * COL_W + PAD * 2;

  const height =
    maxRows * ROW_H + PAD * 2;


  svg.setAttribute(
    "viewBox",
    `0 0 ${width} ${height}`
  );

  transform.style.width =
    width + "px";

  transform.style.height =
    height + "px";

  layer.style.width =
    width + "px";

  layer.style.height =
    height + "px";


  const connectedInCrossMode =
    new Set();

  edgesToDraw.forEach(e => {
    connectedInCrossMode.add(e.source);
    connectedInCrossMode.add(e.target);
  });


  const pos = {};


  levels.forEach((lvl, colIdx) => {
    const nodesInLevel =
      byLevel[lvl];

    const colHeight =
      nodesInLevel.length * ROW_H;

    const yOffset =
      PAD +
      (
        height -
        PAD * 2 -
        colHeight
      ) / 2;


    nodesInLevel.forEach(
      (n, rowIdx) => {
        const x =
          PAD +
          colIdx * COL_W;

        const y =
          yOffset +
          rowIdx * ROW_H;

        pos[n.id] = {
          x,
          y
        };


        const classes = [
          "node"
        ];

        classes.push(
          n.isEntry
            ? "lvl1"
            : n.degree >= 4
              ? "hot"
              : "lvl2"
        );


        if (
          n.id ===
          state.selectedNode
        ) {
          classes.push("active");
        }


        if (
          crossOnly &&
          !connectedInCrossMode.has(
            n.id
          )
        ) {
          classes.push("dimmed");
        }


        const card = el(
          "div",
          {
            class:
              classes.join(" ")
          }
        );


        card.style.left =
          x + "px";

        card.style.top =
          y + "px";

        card.dataset.id =
          n.id;


        card.appendChild(
          el("i", {
            "data-lucide":
              iconFor(n.name)
          })
        );


        const textWrap =
          el("div");

        textWrap.appendChild(
          el("strong", {
            text: n.name
          })
        );

        textWrap.appendChild(
          el("span", {
            text:
              n.group
                ? n.group + "/"
                : (
                    n.isEntry
                      ? "Entry point"
                      : "root"
                  )
          })
        );


        card.appendChild(
          textWrap
        );


        card.addEventListener(
          "click",
          () => selectNode(n.id)
        );


        layer.appendChild(card);
      }
    );
  });


  edgesToDraw.forEach(
    ({ source, target }) => {
      const a = pos[source];
      const b = pos[target];

      if (!a || !b) {
        return;
      }


      const ax =
        a.x + NODE_W;

      const ay =
        a.y + 24;

      const bx =
        b.x;

      const by =
        b.y + 24;

      const midX =
        (ax + bx) / 2;


      const path =
        document.createElementNS(
          "http://www.w3.org/2000/svg",
          "path"
        );


      path.setAttribute(
        "d",
        `M ${ax} ${ay} C ${midX} ${ay}, ${midX} ${by}, ${bx} ${by}`
      );


      svg.appendChild(path);
    }
  );


  if (window.lucide) {
    lucide.createIcons();
  }
}


function setMode(mode, opts = {}) {
  state.mode = mode;

  const modeButton =
    document.querySelector(
      `.mode-btn[data-mode="${mode}"]`
    );

  if (modeButton) {
    setActiveGroup(
      ".graph-modes .mode-btn",
      modeButton
    );
  }


  const viewAsSelect =
    document.getElementById(
      "viewAsSelect"
    );

  if (viewAsSelect) {
    viewAsSelect.value = mode;
  }


  renderMode();


  if (!opts.skipScroll) {
    scrollToId("workspace");
  }
}


function renderMode() {
  const canvas =
    document.getElementById(
      "graphCanvas"
    );

  const mermaidView =
    document.getElementById(
      "mermaidView"
    );

  if (!canvas || !mermaidView) {
    return;
  }


  if (state.mode === "flow") {
    canvas.hidden = true;
    mermaidView.hidden = false;


    if (
      window.mermaid &&
      state.analysis
    ) {
      const container =
        document.getElementById(
          "mermaidRender"
        );

      if (!container) {
        return;
      }

      container.innerHTML = "";

      const id =
        "mmd-" + Date.now();


      mermaid
        .render(
          id,
          state.analysis.mermaid
        )
        .then(({ svg }) => {
          container.innerHTML =
            svg;
        })
        .catch(() => {
          container.textContent =
            "Diagram could not be rendered. See the code above.";
        });
    }

  } else {
    canvas.hidden = false;
    mermaidView.hidden = true;

    if (state.analysis) {
      renderGraph(
        state.analysis
      );
    }
  }
}


function selectNode(id) {
  if (!state.analysis || !id) {
    return;
  }

  state.selectedNode = id;

  const detailPanel =
    document.getElementById(
      "detailPanel"
    );

  if (detailPanel) {
    detailPanel.hidden = false;
  }


  document
    .querySelectorAll(".node")
    .forEach(n => {
      n.classList.toggle(
        "active",
        n.dataset.id === id
      );
    });


  const node =
    state.analysis.nodes.find(
      n => n.id === id
    );

  if (!node) {
    return;
  }


  const edges =
    state.analysis.edges;


  const deps =
    edges
      .filter(
        e => e.source === id
      )
      .map(
        e => e.target
      );


  const usedBy =
    edges
      .filter(
        e => e.target === id
      )
      .map(
        e => e.source
      );


  document.getElementById(
    "detailIcon"
  ).textContent =
    node.lang === "python"
      ? "🐍"
      : "🟨";


  document.getElementById(
    "detailName"
  ).textContent =
    node.name;


  document.getElementById(
    "detailPath"
  ).textContent =
    "/" + node.id;


  document.getElementById(
    "purposeText"
  ).textContent =
    node.purpose ||
    "No description found in this file's docstring/comments.";


  document.getElementById(
    "depCount"
  ).textContent =
    deps.length;


  document.getElementById(
    "usedByCount"
  ).textContent =
    usedBy.length;


  document.getElementById(
    "codePreview"
  ).textContent =
    node.sourcePreview ||
    "// (empty file)";


  document.getElementById(
    "explainText"
  ).textContent =
    "";


  fillRefList(
    "depList",
    deps
  );


  fillRefList(
    "depListFull",
    deps
  );


  fillRefList(
    "usedByList",
    usedBy
  );


  fillRefList(
    "usedByListFull",
    usedBy
  );


  if (window.lucide) {
    lucide.createIcons();
  }
}
function fillRefList(elId, items) {
  const ul =
    document.getElementById(
      elId
    );

  if (!ul) {
    return;
  }

  ul.innerHTML = "";


  if (!items.length) {
    ul.appendChild(
      el("li", {
        text: "None"
      })
    );

    return;
  }


  items.forEach(id => {
    const li = el("li");

    li.appendChild(
      el("i", {
        "data-lucide":
          "file-text"
      })
    );

    li.appendChild(
      el("span", {
        text: id
      })
    );

    li.appendChild(
      el("i", {
        "data-lucide":
          "chevron-right"
      })
    );


    li.addEventListener(
      "click",
      () => selectNode(id)
    );


    ul.appendChild(li);
  });


  if (window.lucide) {
    lucide.createIcons();
  }
}


// ---------- Explain with AI ----------

function renderAiExplanation(
  box,
  explanation
) {
  box.innerHTML = "";

  if (!explanation) {
    box.textContent =
      "No explanation available.";

    return;
  }

  // Backward compatibility if the backend returns plain text.
  if (typeof explanation === "string") {
    box.textContent =
      explanation;

    return;
  }

  const wrapper =
    document.createElement(
      "div"
    );

  wrapper.className =
    "ai-explanation";

  const sections = [
    {
      label: "Role:",
      value: explanation.role,
    },
    {
      label: "Purpose:",
      value: explanation.purpose,
    },
    {
      label: "Dependencies:",
      value: explanation.dependencies,
      type: "dependencies",
    },
    {
      label: "How it fits:",
      value: explanation.how_it_fits,
    },
  ];

  sections.forEach(
    section => {
      const sectionEl =
        document.createElement(
          "div"
        );

      sectionEl.className =
        "ai-explanation-section";

      // Label on its own line
      const label =
        document.createElement(
          "strong"
        );

      label.className =
        "ai-explanation-label";

      label.textContent =
        section.label;

      sectionEl.appendChild(
        label
      );

      // Value below the label
      if (
        section.type ===
        "dependencies"
      ) {
        const dependencies =
          Array.isArray(
            section.value
          )
            ? section.value
            : [];

        if (
          dependencies.length ===
          0
        ) {
          const none =
            document.createElement(
              "div"
            );

          none.className =
            "ai-explanation-value muted";

          none.textContent =
            "None detected";

          sectionEl.appendChild(
            none
          );
        } else {
          const list =
            document.createElement(
              "div"
            );

          list.className =
            "ai-explanation-dependencies";

          dependencies.forEach(
            (
              dependency,
              index
            ) => {
              const item =
                document.createElement(
                  "code"
                );

              item.className =
                "ai-explanation-dependency";

              item.textContent =
                dependency;

              list.appendChild(
                item
              );

              if (
                index <
                dependencies.length - 1
              ) {
                list.appendChild(
                  document.createTextNode(
                    ", "
                  )
                );
              }
            }
          );

          sectionEl.appendChild(
            list
          );
        }
      } else {
        const value =
          document.createElement(
            "div"
          );

        value.className =
          "ai-explanation-value";

        value.textContent =
          section.value ||
          "Not available.";

        sectionEl.appendChild(
          value
        );
      }

      wrapper.appendChild(
        sectionEl
      );
    }
  );

  box.appendChild(
    wrapper
  );

  if (window.lucide) {
    lucide.createIcons();
  }
}


async function explainSelected() {
  if (!state.selectedNode) {
    return;
  }

  const box =
    document.getElementById(
      "explainText"
    );

  if (!box) {
    return;
  }

  box.textContent =
    "Thinking…";

  try {
    const selected =
      state.analysis?.nodes?.find(
        n =>
          n.id ===
          state.selectedNode
      );

    if (!selected) {
      box.textContent =
        "Selected file could not be found.";

      return;
    }

    const edges =
      state.analysis?.edges ||
      [];

    const deps =
      edges
        .filter(
          e =>
            e.source ===
            state.selectedNode
        )
        .map(
          e =>
            e.target
        );

    const usedBy =
      edges
        .filter(
          e =>
            e.target ===
            state.selectedNode
        )
        .map(
          e =>
            e.source
        );

    const node = {
      ...selected,
      deps,
      usedBy,
    };

    const res =
      await fetch(
        `${API_BASE_URL}/api/explain`,
        {
          method: "POST",
          headers: {
            "Content-Type":
              "application/json",
          },
          body: JSON.stringify({
            file:
              state.selectedNode,
            node,
          }),
        }
      );

    const data =
      await res.json();

    if (!res.ok) {
      throw new Error(
        data.error ||
        "Explanation failed."
      );
    }

    renderAiExplanation(
      box,
      data.explanation
    );

  } catch (err) {
    console.error(
      "Explain error:",
      err
    );

    box.textContent =
      err?.message ||
      "Could not reach the backend.";
  }
}


function copyMermaid() {
  const element =
    document.getElementById(
      "mermaidCode"
    );

  if (!element) {
    return;
  }

  const code =
    element.textContent;


  if (
    navigator.clipboard &&
    navigator.clipboard.writeText
  ) {
    navigator.clipboard.writeText(
      code
    );
  }
}


function exportJson() {
  if (!state.analysis) {
    return;
  }


  const blob =
    new Blob(
      [
        JSON.stringify(
          state.analysis,
          null,
          2
        )
      ],
      {
        type:
          "application/json"
      }
    );


  const a =
    document.createElement(
      "a"
    );


  a.href =
    URL.createObjectURL(
      blob
    );


  a.download =
    `${
      state.analysis.projectName ||
      "codeorbit"
    }-analysis.json`;


  a.click();


  setTimeout(() => {
    URL.revokeObjectURL(
      a.href
    );
  }, 1000);
}


function resetApp() {
  state.analysis = null;
  state.selectedNode = null;
  state.mode = "architecture";


  const zipInput =
    document.getElementById(
      "zipInput"
    );

  if (zipInput) {
    zipInput.value = "";
  }


  const uploadStatus =
    document.getElementById(
      "uploadStatus"
    );

  if (uploadStatus) {
    uploadStatus.textContent = "";
  }


  document.getElementById(
    "projectHeader"
  ).hidden = true;


  document.getElementById(
    "workspace"
  ).hidden = true;


  document.getElementById(
    "bottomGrid"
  ).hidden = true;


  document.getElementById(
    "emptyState"
  ).hidden = false;


  document.getElementById(
    "nodeLayer"
  ).innerHTML = "";


  document.getElementById(
    "connectors"
  ).innerHTML = "";


  setZoom(1);


  window.scrollTo({
    top: 0,
    behavior: "smooth"
  });
}


// ---------- zoom ----------

let zoomLevel = 1;


function setZoom(v) {
  zoomLevel =
    Math.min(
      1.6,
      Math.max(
        0.5,
        v
      )
    );


  const transform =
    document.getElementById(
      "graphTransform"
    );

  if (transform) {
    transform.style.transform =
      `scale(${zoomLevel})`;
  }


  const zoomLabel =
    document.getElementById(
      "zoomLevel"
    );

  if (zoomLabel) {
    zoomLabel.textContent =
      Math.round(
        zoomLevel * 100
      ) + "%";
  }
}


function zoom(delta) {
  setZoom(
    zoomLevel + delta
  );
}


// ---------- fullscreen ----------

function toggleFullscreen() {
  const card =
    document.querySelector(
      ".graph-card"
    );

  if (!card) {
    return;
  }


  const isFullscreen =
    !!(
      document.fullscreenElement ||
      document.webkitFullscreenElement
    );


  if (!isFullscreen) {
    const request =
      card.requestFullscreen ||
      card.webkitRequestFullscreen;

    if (request) {
      request.call(card);
    }

  } else {
    const exit =
      document.exitFullscreen ||
      document.webkitExitFullscreen;

    if (exit) {
      exit.call(document);
    }
  }
}


function syncFullscreenIcon() {
  const active =
    !!(
      document.fullscreenElement ||
      document.webkitFullscreenElement
    );


  const btn =
    document.getElementById(
      "fullscreenBtn"
    );

  if (!btn) {
    return;
  }


  const icon =
    btn.querySelector("i");


  if (icon) {
    icon.setAttribute(
      "data-lucide",
      active
        ? "minimize"
        : "maximize"
    );
  }


  btn.classList.toggle(
    "active",
    active
  );


  if (window.lucide) {
    lucide.createIcons();
  }
}
