# 3Pass Literature Review: Living Synthesis Document

## System Architecture & Provenance Rules
Every claim, insight, or evidence summary in this document includes strict provenance:
> **Format**: `[^SourceID] Source ID | Drive Path / ID | Pass Level (P1/P2/P3)`

---

## 1. Executive Summary & Synthesis
This living document synthesizes a growing corpus of 785 academic sources hosted on Google Drive (`G:\My Drive\Scholar Explorer\papers\`). 
The synthesis is organized around three interdependent research axes:
1. **Evidence Evaluation**: Moving beyond static benchmarks and hallucination-prone text generation by integrating neurosymbolic validation, verifiable reasoning traces, and Bayesian human-AI synergy modeling.
2. **Learner Agency**: Ensuring that AI tools in instructional and educational contexts scaffold learner decision-making, autonomy, and metacognitive reflection rather than supplanting human cognitive effort.
3. **Traceable Workflows**: Structuring agentic execution pipelines through verifiable reasoning-acting loops (ReAct), symbolic constraints, and audit-grade telemetry event streams.

---

## 2. Evidence Evaluation & Verifiable Reasoning

### 2.1 Neurosymbolic Grounding & Explainable QA in Education
- **Bridging LLMs and Symbolic Reasoning in Educational QA Systems: Insights from the XAI Challenge at IJCNN 2025**
  - *Authors & Year*: Long S. T. Nguyen et al. (2025)
  - *Core Methodology*: Combines Large Language Models with symbolic reasoning architectures to address hallucination and lack of interpretability in educational question-answering systems. Benchmarked on real-world educational problem sets.
  - *Key Finding*: Pure LLMs exhibit high rates of ungrounded factual generation in pedagogical explanations; coupling LLMs with symbolic rule validators reduces spurious reasoning while providing verifiable step-by-step proofs for students.
  - *Relevance to Evidence Evaluation (4/5)*: Provides a concrete architectural pattern for adding verification guardrails that evaluate model claims against symbolic knowledge graphs before delivering pedagogical feedback.
  - *Provenance*: `[^SRC-0002] SRC-0002 | G:\My Drive\Scholar Explorer\papers\Copy of 2508.01263v1.pdf | P2`

### 2.2 Quantifying Human-AI Synergy via Psychometric Modeling
- **Quantifying Human-AI Synergy**
  - *Core Methodology*: Introduces a Bayesian Item Response Theory (IRT) framework to isolate and quantify human-AI collaborative synergy. Unlike static benchmarks that treat human and model in isolation, this framework models collaborative performance as a joint latent process, controlling for task difficulty and individual baseline competence ($n=667$).
  - *Key Finding*: Human-AI collaboration produces statistically significant synergy, but the lift is non-uniform: assistive lift is highest when human task difficulty exceeds human solo capability, provided the AI provides verifiable intermediate reasoning.
  - *Relevance to Evidence Evaluation (3/5)*: Establishes a rigorous psychometric measurement framework for evaluating whether an AI agent actually augments human judgment or merely substitutes for it.
  - *Provenance*: `[^SRC-0003] SRC-0003 | G:\My Drive\Scholar Explorer\papers\Copy of 7799_Quantifying_Human_AI_Syne.pdf | P2`

### 2.3 Aligning Models with Symbolic Domain Feedback
- **RLSF: Fine-tuning LLMs via Symbolic Feedback**
  - *Authors & Year*: Piyush Jha, Prithwish Jana, Pranavkrishna Suresh, Arnav Arora, Vijay Ganesh (2025)
  - *Core Methodology*: Proposes Reinforcement Learning from Symbolic Feedback (RLSF) as an alternative to Reinforcement Learning from Human Feedback (RLHF). Utilizes automated theorem provers, SAT/SMT solvers, and formal verifiers to supply non-differentiable reward signals directly into the model alignment loop.
  - *Key Finding*: Symbolic feedback eliminates reward hacking and alignment drift common in neural reward models, ensuring that generated traces strictly conform to logical invariants.
  - *Relevance to Evidence Evaluation (3/5)*: Demonstrates that guardrails can be formalized into automated verifiers, enabling provable correctness in high-stakes reasoning pipelines.
  - *Provenance*: `[^SRC-0009] SRC-0009 | G:\My Drive\Scholar Explorer\papers\Copy of 2405.16661v3.pdf | P2`

### 2.4 Multi-Step Reasoning Traces as Transparent Evidence
- **Chain-of-Thought Prompting Elicits Reasoning in Large Language Models**
  - *Authors & Year*: Jason Wei et al. (2022/2023)
  - *Core Methodology*: Introduces prompt-based elicitation of explicit, decomposed intermediate reasoning steps prior to final answer generation across arithmetic, commonsense, and symbolic reasoning tasks.
  - *Key Finding*: Multi-step reasoning traces drastically improve complex problem solving while transforming black-box model predictions into auditable, human-inspectable chains of inference.
  - *Relevance to Evidence Evaluation (3/5)*: Forms the evidentiary substrate for all modern agentic verification and critique workflows.
  - *Provenance*: `[^SRC-0010] SRC-0010 | G:\My Drive\Scholar Explorer\papers\Copy of 2201.11903v6.pdf | P2`

---

## 3. Learner Agency & Human-AI Collaboration

### 3.1 Human-Centered Policy & Pedagogical Sovereignty
- **Artificial Intelligence and the Future of Teaching and Learning: Insights and Recommendations**
  - *Authors & Year*: Office of Educational Technology, U.S. Department of Education (2023)
  - *Core Findings*: Establishes core policy and design mandates for educational AI: (1) Human-in-the-loop requirement, (2) prioritizing learner agency and educator authority, and (3) mitigating surveillance and privacy risks.
  - *Pedagogical Impact*: AI must be designed as an *amplifier of human agency* rather than an automation pipeline that bypasses student metacognition, self-regulation, or creative inquiry.
  - *Relevance to Learner Agency (3/5)*: Sets policy benchmarks for ethical learning systems that support learner autonomy and transparent data governance.
  - *Provenance*: `[^SRC-0023] SRC-0023 | G:\My Drive\Scholar Explorer\papers\ai-report.pdf | P2`

### 3.2 Transactivity and Knowledge Co-Construction
- **Transactivity and Knowledge Co-Construction in Collaborative Learning**
  - *Authors & Year*: International Society of the Learning Sciences (CSCL 2023)
  - *Core Methodology*: Investigates transactive discourse—how learners build upon, critique, and integrate peer contributions during collaborative problem solving.
  - *Key Finding*: High levels of transactivity correlate directly with deep conceptual learning; when cognitive agents participate in groups, they must prompt and scaffold transactive exchanges rather than simply presenting answers.
  - *Relevance to Learner Agency (4/5)*: Informs the design of agentic tutors that prompt learners to articulate, defend, and refine their own hypotheses.
  - *Provenance*: `[^SRC-0041] SRC-0041 | G:\My Drive\Scholar Explorer\papers\CSCL2023_337-346.pdf | P2`

---

## 4. Traceable Workflows & Telemetry Architectures

### 4.1 Synergizing Reasoning and Acting in Autonomous Agents
- **ReAct: Synergizing Reasoning and Acting in Language Models**
  - *Authors & Year*: Shunyu Yao et al. (2023)
  - *Core Methodology*: Integrates dynamic reasoning traces ("thoughts") with external environment interactions ("actions") in an interleaved loop: `Thought -> Action -> Observation -> Thought`.
  - *Key Finding*: Coupling reasoning traces to external actions overcomes error propagation in pure chain-of-thought and reduces hallucination during tool invocation by anchoring thoughts to real environment observations.
  - *Relevance to Traceable Workflows (4/5)*: The ReAct cycle serves as the standard telemetry log format for recording provenance and decision paths across multi-agent workflows.
  - *Provenance*: `[^SRC-0006] SRC-0006 | G:\My Drive\Scholar Explorer\papers\Copy of 2210.03629v3.pdf | P2`

### 4.2 Autonomous Document-to-Codebase Synthesis
- **DeepCode: Open Agentic Coding**
  - *Authors & Year*: Zongwei Li, Zhonghang Li, Zirui Guo, Xubin Ren, Chao Huang (2025)
  - *Core Methodology*: Develops an autonomous multi-agent software engineering framework tailored for complex document-to-code synthesis (e.g. translating scientific literature into verified runnable codebases).
  - *Key Finding*: Implements a hierarchical agent memory and context decomposition architecture to overcome context window saturation during paper-to-implementation reproduction.
  - *Relevance to Traceable Workflows (3/5)*: Provides an architectural reference for maintaining end-to-end lineage between research literature specifications and verified software artifacts.
  - *Provenance*: `[^SRC-0004] SRC-0004 | G:\My Drive\Scholar Explorer\papers\Copy of 2512.07921v1.pdf | P2`

---

## 5. Methodological & Synthesis Matrix

| Source ID | Author & Year | Focus / Core Framework | Evidence Eval (1-5) | Learner Agency (1-5) | Traceable Workflows (1-5) | Triage Decision | Pass Level |
|-----------|---------------|------------------------|---------------------|----------------------|---------------------------|-----------------|------------|
| `SRC-0002` | Nguyen et al. (2025) | Neurosymbolic Educational QA / XAI | 4 | 3 | 2 | **Include** | **P2 done** |
| `SRC-0003` | Anon (2024/2025) | Bayesian IRT for Human-AI Synergy | 3 | 1 | 1 | **Include** | **P2 done** |
| `SRC-0004` | Li et al. (2025) | DeepCode: Autonomous Paper-to-Code Synthesis | 3 | 1 | 2 | **Include** | **P2 done** |
| `SRC-0006` | Yao et al. (2023) | ReAct: Interleaved Reasoning and Acting | 3 | 1 | 4 | **Include** | **P2 done** |
| `SRC-0009` | Jha et al. (2025) | RLSF: Symbolic Solvers for LLM Alignment | 3 | 1 | 2 | **Include** | **P2 done** |
| `SRC-0010` | Wei et al. (2022) | Chain-of-Thought Intermediate Traces | 3 | 1 | 2 | **Include** | **P2 done** |
| `SRC-0021` | Educational Tech (2022) | EdTech Trends & Systematic Mapping | 1 | 4 | 2 | **Include** | **P1 done** |
| `SRC-0023` | US Dept of Ed (2023) | Future of Teaching & Learning (Policy Mandates) | 2 | 3 | 1 | **Include** | **P2 done** |
| `SRC-0024` | UNESCO (2023) | Generative AI in Education & Research | 1 | 3 | 1 | **Include** | **P1 done** |
| `SRC-0025` | SAFE Framework (2023) | Benchmarking & Evaluating Educational AI | 2 | 3 | 2 | **Include** | **P1 done** |
| `SRC-0041` | ISLS / CSCL (2023) | Transactivity & Knowledge Co-Construction | 1 | 4 | 2 | **Include** | **P2 done** |
| `SRC-0042` | ACM (2023) | Learner Agency & Scaffolding in Digital Tools | 1 | 5 | 1 | **Include** | **P1 done** |

---

## 6. Access Gaps & Exclusion Log
### 6.1 Inaccessible / Corrupted Files
- **`SRC-0304`**: `2023 W-2.pdf`
  - *Original Location*: `C:\Users\Ashley\Downloads\2023 W-2.pdf`
  - *Reason*: Encrypted / password-protected or corrupt PDF stream; unreadable by parser. Also non-academic.

### 6.2 Excluded Files (32 Items)
Triage successfully detected and filtered out administrative records, course syllabi, and non-academic forms:
- **Administrative Forms & Statements**: Bereavement leave forms (`SRC-0446`), release forms (`SRC-0399`), personal receipts/tax docs (`SRC-0304`).
- **Course Syllabi & Guides**: Course outlines and syllabi (e.g., `SRC-0451: EDTECH 603 Syllabus FA25`).
- **Marketing / Flier Material**: Single-page panel fliers, event announcements (`SRC-0048`).
- **Corrupted / Empty Snippets**: Incomplete downloads and unreadable fragments.

### 6.3 Inclusion Summary
- **Total Ingested Sources**: 785
- **Academic Research Included**: 269 papers (High relevance to Evidence Evaluation, Learner Agency, or Traceable Workflows)
- **Contextual / Background (Maybe)**: 484 papers
- **Excluded**: 32 files
- **Inaccessible / Access Gaps**: 1 file (`SRC-0304`)

