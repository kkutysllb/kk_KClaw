---
name: bioinformatics
description: 通往来自 bioSkills 和 ClawBio 的 400+ 生物信息学技能的大门。涵盖基因组学、转录组学、单细胞、变异调用、药物基因组学、宏基因组学、结构生物学等。按需获取领域特定参考材料。
version: 1.0.0
platforms: [linux, macos]
metadata:
  kclaw:
    tags: [bioinformatics, genomics, sequencing, biology, research, science]
    category: research
---

# 生物信息学技能网关

当被问及生物信息学、基因组学、测序、变异调用、基因表达、单细胞分析、蛋白质结构、药物基因组学、宏基因组学、系统发育学或任何计算生物学任务时使用。

此技能是两个开源生物信息学技能库的网关。它不是捆绑数百个领域特定的技能，而是为它们建立索引并按需获取您需要的内容。

## 来源

◆ **bioSkills** — 385 个参考技能（代码模式、参数指南、决策树）
  仓库：https://github.com/GPTomics/bioSkills
  格式：每个主题一个 SKILL.md，包含代码示例。Python/R/CLI。

◆ **ClawBio** — 33 个可运行管道技能（可执行脚本、可重现性捆绑包）
  仓库：https://github.com/ClawBio/ClawBio
  格式：带演示的 Python 脚本。每个分析导出 report.md + commands.sh + environment.yml。

## 如何获取和使用技能

1. 从下面的索引中识别领域和技能名称。
2. 克隆相关仓库（浅克隆以节省时间）：
   ```bash
   # bioSkills（参考材料）
   git clone --depth 1 https://github.com/GPTomics/bioSkills.git /tmp/bioSkills

   # ClawBio（可运行管道）
   git clone --depth 1 https://github.com/ClawBio/ClawBio.git /tmp/ClawBio
   ```
3. 读取特定技能：
   ```bash
   # bioSkills — 每个技能在：<category>/<skill-name>/SKILL.md
   cat /tmp/bioSkills/variant-calling/gatk-variant-calling/SKILL.md

   # ClawBio — 每个技能在：skills/<skill-name>/
   cat /tmp/ClawBio/skills/pharmgx-reporter/README.md
   ```
4. 将获取的技能作为参考材料遵循。这些不是 KClaw 格式的技能 — 将它们视为专家领域指南。它们包含正确的参数、正确的工具标志和经过验证的管道。

## 按领域的技能索引

### 序列基础
bioSkills:
  sequence-io/ — read-sequences, write-sequences, format-conversion, batch-processing, compressed-files, fastq-quality, filter-sequences, paired-end-fastq, sequence-statistics
  sequence-manipulation/ — seq-objects, reverse-complement, transcription-translation, motif-search, codon-usage, sequence-properties, sequence-slicing
ClawBio:
  seq-wrangler — 序列 QC、比對和 BAM 处理（包装 FastQC、BWA、SAMtools）

### 读取 QC 与比对
bioSkills:
  read-qc/ — quality-reports, fastp-workflow, adapter-trimming, quality-filtering, umi-processing, contamination-screening, rnaseq-qc
  read-alignment/ — bwa-alignment, star-alignment, hisat2-alignment, bowtie2-alignment
  alignment-files/ — sam-bam-basics, alignment-sorting, alignment-filtering, bam-statistics, duplicate-handling, pileup-generation

### 变异调用与注释
bioSkills:
  variant-calling/ — gatk-variant-calling, deepvariant, variant-calling (bcftools), joint-calling, structural-variant-calling, filtering-best-practices, variant-annotation, variant-normalization, vcf-basics, vcf-manipulation, vcf-statistics, consensus-sequences, clinical-interpretation
ClawBio:
  vcf-annotator — 带祖先感知上下文的 VEP + ClinVar + gnomAD 注释
  variant-annotation — 变异注释管道

### 差异表达（Bulk RNA-seq）
bioSkills:
  differential-expression/ — deseq2-basics, edger-basics, batch-correction, de-results, de-visualization, timeseries-de
  rna-quantification/ — alignment-free-quant (Salmon/kallisto), featurecounts-counting, tximport-workflow, count-matrix-qc
  expression-matrix/ — counts-ingest, gene-id-mapping, metadata-joins, sparse-handling
ClawBio:
  rnaseq-de — 带有 QC、标准化和可视化的完整 DE 管道
  diff-visualizer — DE 结果的丰富可视化和报告

### 单细胞 RNA-seq
bioSkills:
  single-cell/ — preprocessing, clustering, batch-integration, cell-annotation, cell-communication, doublet-detection, markers-annotation, trajectory-inference, multimodal-integration, perturb-seq, scatac-analysis, lineage-tracing, metabolite-communication, data-io
ClawBio:
  scrna-orchestrator — 完整 Scanpy 管道（QC、聚类、标记物、注释）
  scrna-embedding — 基于 scVI 的潜在嵌入和批次集成

### 空间转录组学
bioSkills:
  spatial-transcriptomics/ — spatial-data-io, spatial-preprocessing, spatial-domains, spatial-deconvolution, spatial-communication, spatial-neighbors, spatial-statistics, spatial-visualization, spatial-multiomics, spatial-proteomics, image-analysis

### 表观基因组学
bioSkills:
  chip-seq/ — peak-calling, differential-binding, motif-analysis, peak-annotation, chipseq-qc, chipseq-visualization, super-enhancers
  atac-seq/ — atac-peak-calling, atac-qc, differential-accessibility, footprinting, motif-deviation, nucleosome-positioning
  methylation-analysis/ — bismark-alignment, methylation-calling, dmr-detection, methylkit-analysis
  hi-c-analysis/ — hic-data-io, tad-detection, loop-calling, compartment-analysis, contact-pairs, matrix-operations, hic-visualization, hic-differential
ClawBio:
  methylation-clock — 表观遗传年龄估计

### 药物基因组学与临床
bioSkills:
  clinical-databases/ — clinvar-lookup, gnomad-frequencies, dbsnp-queries, pharmacogenomics, polygenic-risk, hla-typing, variant-prioritization, somatic-signatures, tumor-mutational-burden, myvariant-queries
ClawBio:
  pharmgx-reporter — 来自 23andMe/AncestryDNA 的 PGx 报告（12 个基因、31 个 SNP、51 种药物）
  drug-photo — 药物照片 → 个性化 PGx 剂量卡（通过视觉）
  clinpgx — 用于基因-药物数据和 CPIC 指南的 ClinPGx API
  gwas-lookup — 跨 9 个基因组数据库的联邦变异查找
  gwas-prs — 来自消费者基因数据的 polygenic 风险评分
  nutrigx_advisor — 来自消费者基因数据的个性化营养

### 种群遗传学与 GWAS
bioSkills:
  population-genetics/ — association-testing (PLINK GWAS), plink-basics, population-structure, linkage-disequilibrium, scikit-allel-analysis, selection-statistics
  causal-genomics/ — mendelian-randomization, fine-mapping, colocalization-analysis, mediation-analysis, pleiotropy-detection
  phasing-imputation/ — haplotype-phasing, genotype-imputation, imputation-qc, reference-panels
ClawBio:
  claw-ancestry-pca — 针对 SGDP 参考面板的祖先 PCA

### 宏基因组学与微生物组
bioSkills:
  metagenomics/ — kraken-classification, metaphlan-profiling, abundance-estimation, functional-profiling, amr-detection, strain-tracking, metagenome-visualization
  microbiome/ — amplicon-processing, diversity-analysis, differential-abundance, taxonomy-assignment, functional-prediction, qiime2-workflow
ClawBio:
  claw-metagenomics — 鸟枪宏基因组学分析（分类学、抗生素耐药组、功能途径）

### 基因组组装与注释
bioSkills:
  genome-assembly/ — hifi-assembly, long-read-assembly, short-read-assembly, metagenome-assembly, assembly-polishing, assembly-qc, scaffolding, contamination-detection
  genome-annotation/ — eukaryotic-gene-prediction, prokaryotic-annotation, functional-annotation, ncrna-annotation, repeat-annotation, annotation-transfer
  long-read-sequencing/ — basecalling, long-read-alignment, long-read-qc, clair3-variants, structural-variants, medaka-polishing, nanopore-methylation, isoseq-analysis

### 结构生物学与化学信息学
bioSkills:
  structural-biology/ — alphafold-predictions, modern-structure-prediction, structure-io, structure-navigation, structure-modification, geometric-analysis
  chemoinformatics/ — molecular-io, molecular-descriptors, similarity-searching, substructure-search, virtual-screening, admet-prediction, reaction-enumeration
ClawBio:
  struct-predictor — 本地 AlphaFold/Boltz/Chai 结构预测与比较

### 蛋白质组学
bioSkills:
  proteomics/ — data-import, peptide-identification, protein-inference, quantification, differential-abundance, dia-analysis, ptm-analysis, proteomics-qc, spectral-libraries
ClawBio:
  proteomics-de — 蛋白质组学差异表达

### 途径分析与基因网络
bioSkills:
  pathway-analysis/ — go-enrichment, gsea, kegg-pathways, reactome-pathways, wikipathways, enrichment-visualization
  gene-regulatory-networks/ — scenic-regulons, coexpression-networks, differential-networks, multiomics-grn, perturbation-simulation

### 免疫信息学
bioSkills:
  immunoinformatics/ — mhc-binding-prediction, epitope-prediction, neoantigen-prediction, immunogenicity-scoring, tcr-epitope-binding
  tcr-bcr-analysis/ — mixcr-analysis, scirpy-analysis, immcantation-analysis, repertoire-visualization, vdjtools-analysis

### CRISPR 与基因组工程
bioSkills:
  crispr-screens/ — mageck-analysis, jacks-analysis, hit-calling, screen-qc, library-design, crispresso-editing, base-editing-analysis, batch-correction
  genome-engineering/ — grna-design, off-target-prediction, hdr-template-design, base-editing-design, prime-editing-design

### 工作流管理
bioSkills:
  workflow-management/ — snakemake-workflows, nextflow-pipelines, cwl-workflows, wdl-workflows
ClawBio:
  repro-enforcer — 将任何分析导出为可重现性捆绑包（Conda 环境 + Singularity + 校验和）
  galaxy-bridge — 从 usegalaxy.org 访问 8,000+ Galaxy 工具

### 专业领域
bioSkills:
  alternative-splicing/ — splicing-quantification, differential-splicing, isoform-switching, sashimi-plots, single-cell-splicing, splicing-qc
  ecological-genomics/ — edna-metabarcoding, landscape-genomics, conservation-genetics, biodiversity-metrics, community-ecology, species-delimitation
  epidemiological-genomics/ — pathogen-typing, variant-surveillance, phylodynamics, transmission-inference, amr-surveillance
  liquid-biopsy/ — cfdna-preprocessing, ctdna-mutation-detection, fragment-analysis, tumor-fraction-estimation, methylation-based-detection, longitudinal-monitoring
  epitranscriptomics/ — m6a-peak-calling, m6a-differential, m6anet-analysis, merip-preprocessing, modification-visualization
  metabolomics/ — xcms-preprocessing, metabolite-annotation, normalization-qc, statistical-analysis, pathway-mapping, lipidomics, targeted-analysis, msdial-preprocessing
  flow-cytometry/ — fcs-handling, gating-analysis, compensation-transformation, clustering-phenotyping, differential-analysis, cytometry-qc, doublet-detection, bead-normalization
  systems-biology/ — flux-balance-analysis, metabolic-reconstruction, gene-essentiality, context-specific-models, model-curation
  rna-structure/ — secondary-structure-prediction, ncrna-search, structure-probing

### 数据可视化与报告
bioSkills:
  data-visualization/ — ggplot2-fundamentals, heatmaps-clustering, volcano-customization, circos-plots, genome-browser-tracks, interactive-visualization, multipanel-figures, network-visualization, upset-plots, color-palettes, specialized-omics-plots, genome-tracks
  reporting/ — rmarkdown-reports, quarto-reports, jupyter-reports, automated-qc-reports, figure-export
ClawBio:
  profile-report — 分析概况报告
  data-extractor — 从科学图形图像中提取数值数据（通过视觉）
  lit-synthesizer — PubMed/bioRxiv 搜索、摘要、引用图
  pubmed-summariser — 基因/疾病 PubMed 搜索与结构化简报

### 数据库访问
bioSkills:
  database-access/ — entrez-search, entrez-fetch, entrez-link, blast-searches, local-blast, sra-data, geo-data, uniprot-access, batch-downloads, interaction-databases, sequence-similarity
ClawBio:
  ukb-navigator — 跨 12,000+ UK Biobank 字段的语义搜索
  clinical-trial-finder — 临床试验发现

### 实验设计
bioSkills:
  experimental-design/ — power-analysis, sample-size, batch-design, multiple-testing

### 组学机器学习
bioSkills:
  machine-learning/ — omics-classifiers, biomarker-discovery, survival-analysis, model-validation, prediction-explanation, atlas-mapping
ClawBio:
  claw-semantic-sim — 疾病文献的语义相似性指数（PubMedBERT）
  omics-target-evidence-mapper — 跨组学来源聚合目标水平证据

## 环境设置

这些技能假设有生物信息学工作站。常见依赖：

```bash
# Python
pip install biopython pysam cyvcf2 pybedtools pyBigWig scikit-allel anndata scanpy mygene

# R/Bioconductor
Rscript -e 'BiocManager::install(c("DESeq2","edgeR","Seurat","clusterProfiler","methylKit"))'

# CLI 工具（Ubuntu/Debian）
sudo apt install samtools bcftools ncbi-blast+ minimap2 bedtools

# CLI 工具（macOS）
brew install samtools bcftools blast minimap2 bedtools

# 或通过 Conda（推荐用于可重现性）
conda install -c bioconda samtools bcftools blast minimap2 bedtools fastp kraken2
```

## 陷阱

- 获取的技能不是 KClaw SKILL.md 格式。它们使用自己的结构（bioSkills：代码模式 cookbook；ClawBio：README + Python 脚本）。将它们作为专家参考材料阅读。
- bioSkills 是参考指南 — 它们显示正确的参数和代码模式，但不是可执行的管道。
- ClawBio 技能是可执行的 — 许多有 `--demo` 标志，可以直接运行。
- 两个仓库都假设已安装生物信息学工具。运行管道前检查先决条件。
- 对于 ClawBio，先在克隆的仓库中运行 `pip install -r requirements.txt`。
- 基因组数据文件可能非常大。在下载参考基因组、SRA 数据集或构建索引时注意磁盘空间。
