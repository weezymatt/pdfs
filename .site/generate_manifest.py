#!/usr/bin/env python3
"""Generate manifest.json for the tpn/pdfs browsing site (https://tpn.github.io/pdfs/).

Stdlib only.  Two input modes:

  * local git (default): reads `git ls-tree -r -l` from the working copy
  * --tree-json FILE:    reads a GitHub Trees API response
                         (GET /repos/OWNER/REPO/git/trees/SHA?recursive=1),
                         which carries per-blob sizes without any blob fetches
                         -- the cheap path used in CI, where the clone is
                         blobless and `ls-tree -l` would lazy-fetch every blob.

The collection's hand-curated filename grammar:

    Title - Qualifier(s) - Year (original-filename-or-doc-id).pdf

with a leading-year variant used in Perfect Hashing/:

    YYYY - Title (id).pdf
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SITE_FILES = ("index.html", "app.js", "style.css")

INCLUDE_EXT = {"pdf", "ppt", "pptx", "doc", "docx", "xlsx", "txt"}
EXCLUDE_TOP = {"x86asm.net", ".site", ".github"}
EXCLUDE_FILES = {"README.md", ".gitignore"}

TYPES = ["paper", "slides", "manual", "book", "report", "thesis", "data", "text"]

EXT_RE = re.compile(r"\.(pdf|pptx?|docx?|xlsx|txt)$", re.I)
YEAR_RE = re.compile(r"\b(19\d{2}|20[0-2]\d)\b")
ARXIV_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
TRAIL_RE = re.compile(r"\s*\(([^()]{1,80})\)$")

MONTHS = {
    "jan", "january", "feb", "february", "mar", "march", "apr", "april",
    "may", "jun", "june", "jul", "july", "aug", "august",
    "sep", "sept", "september", "oct", "october", "nov", "november",
    "dec", "december",
}

# Topic rules are matched case-insensitively against the full relative path,
# so folder names count.  A file collects every matching topic; none -> misc.

# TODO: NLP, AI, Linguistics
TOPIC_RULES = [
    ("hashing", r"hash|cuckoo|hopscotch|sha-?\d|blake2|keccak|md5|\bcrc\b|checksum"),
    ("data-structures", r"b-?trees?\b|\btries?\b|\bheaps?\b|skip list|bloom|succinct|"
                        r"data structure|linked list|red-black|priority queue|treap|"
                        r"\bqueues?\b|\bstacks?\b|masstree|\blsm\b|radix"),
    ("algorithms", r"algorithm|\bsort(ing)?\b|complexity|dynamic programming|"
                   r"\b(hyper)?graphs?\b|combinator|np-(hard|complete)|\bgreedy\b|"
                   r"matching|\bsearch(ing)?\b|partition"),
    ("gpu-graphics", r"\bgpus?\b|cuda|opencl|shader|graphics|vulkan|direct3d|directx|"
                     r"rasteriz|ray ?trac|path ?trac|render|opengl|nvidia|\bgtc\b|radiance|"
                     r"\bvr\b|texture|video"),
    ("cpu-arch", r"x86|x64\b|\barm\b|risc|\bisa\b|microarchitect|\bsimd\b|\bavx\b|\bsse\d?\b|"
                 r"branch predict|opcode|ia-32|ia-64|amd64|amd\b|itanium|instruction|\bintel\b|"
                 r"processor|\bcpu\b|microcode|assembly|assembler|alpha (architecture|axp)|"
                 r"\baxp\b|mips|superscalar|out-of-order|systolic|\d{2}-bit|firmware|"
                 r"\bacpi\b|\buefi\b|\bbios\b|emulat|\bvliw\b|haswell|sparc|pci ?e(xpress)?|"
                 r"cordic|prefetch|\bvax\b|\bfpga\b"),
    ("compilers-langs", r"compil|llvm|\bjit\b|interpret|garbage collect|parsing|parser|"
                        r"type system|c\+\+|cppcon|\brust\b|python|\bjava\b|haskell|scala|"
                        r"\blisp\b|scheme|fortran|\bapl\b|smalltalk|language|\bclang\b|"
                        r"\bgcc\b|linker|loader|static analys|program analys|"
                        r"value numbering|name mangling|program synthesis|"
                        r"reference counting|rewriting|coccinelle|numba|javascript|"
                        r"ecma|shared librar"),
    ("databases", r"database|\bsql\b|oracle|postgres|\bolap\b|\boltp\b|quer(y|ies)|"
                  r"transaction|column(ar| store)|vertica|data warehouse|\bjoins?\b|"
                  r"\bindex(es|ing)?\b|\bdbms\b|hekaton|vldb|sigmod|cidr\b|relational|"
                  r"\bcodd\b|\baries\b"),
    ("machine-learning", r"neural|deep learning|machine learning|tensor|back-?prop|gradient|"
                         r"regression|transformer|\bllm\b|xgboost|boosting|classif|"
                         r"reinforcement|convolutional|\bgan\b|autoencoder|embedding|"
                         r"attention|bert\b|gpt\b|perceptron|\bbandit\b|automl|yolo|"
                         r"alphafold|deepseek|\bmamba\b|softmax|layer normaliz|gelu|"
                         r"adaboost|support vector|word representation|word2vec|\brag\b|"
                         r"sequence model|state space|image (recognition|restoration)|"
                         r"super-?resolution|adversarial|kalman"),
    ("windows", r"windows|win32|\bnt\b|\bwdf\b|\bwdm\b|pe.?coff|minifilter|\bioctl\b|"
                r"sysinternals|msdn|microsoft|ntfs|\betw\b|windbg|\bcom\b|\bclr\b|\bwmi\b|"
                r"component object model|\bdcom\b|\bpe\b|\bdlls?\b|winsock"),
    ("unix-linux", r"\bunix\b|linux|\bbsd\b|posix|solaris|tru64|\bvms\b|plan 9|beos|"
                   r"freebsd|openbsd|illumos|systemd|\bebpf\b|\bmach\b|mac os|macos|"
                   r"darwin|\bxnu\b|\birix\b|\baix\b|dragonfly|reactos"),
    ("os-kernel", r"operating system|kernel|syscall|system call|schedul|hypervisor|"
                  r"virtualiz|\bvmm\b|microkernel|exokernel|interrupt|context switch|"
                  r"device driver|drivers?\b|\bboot\b"),
    ("networking", r"network|\btcp\b|\budp\b|\brdma\b|\bdpdk\b|packet|ethernet|100g\b|"
                   r"socket|infiniband|\bnics?\b|\bhttp\b|\bdns\b|routing|congestion|"
                   r"fibre channel|iscsi|\bsan\b|\bnas\b|802\.\d|\baltq\b|zeromq|"
                   r"protocol|link aggregation"),
    ("concurrency", r"concurren|lock-?free|wait-?free|atomic|mutex|synchroniz|parallel|"
                    r"thread|transactional memory|spinlock|\blocks?\b|\bbarriers?\b|"
                    r"memory (model|consistency|order)|\brcu\b|epoch|hazard pointer|"
                    r"futex|semaphore|compare-and-swap|deadlock|interleav|coroutine|"
                    r"\bfibers?\b|completion port"),
    ("perf-tracing", r"\betw\b|xperf|\btrac(e|ing)\b|profil|benchmark|performance|latency|"
                     r"flame graph|vtune|\bperf\b|instrument|optimiz|tuning|scalab|"
                     r"debugg|crash dump|diagnos"),
    ("compression", r"compress|\blz\w*\b|zstd|huffman|entropy cod|codec|deflate|brotli|"
                    r"\bzip\b|arithmetic cod|succinct|\bcod(es|ing)\b|unary|golomb|elias"),
    ("math-stats", r"probab|statist|bayes|markov|monte carlo|linear algebra|numeric|"
                   r"random|\brngs?\b|matri(x|ces)|calculus|algebra|stochastic|"
                   r"distribution|combinatoric|number theor|\bprng\b|floating.?point|"
                   r"xorshift|mersenne|information theor|entropy\b|shannon|\bfft\b|"
                   r"fourier|polynomial|mathemat|theorem|\bprimes?\b|trigonometr|"
                   r"\btrig\b|differential equation|simplex|integral|geometr|"
                   r"\bblas\b|lapack|quantile"),
    ("strings-text", r"\bstrings?\b|suffix|fm-index|regex|regular expression|unicode|"
                     r"\butf-?\d*\b|full-?text|pattern match|substring|text (search|processing)|"
                     r"edit distance|levenshtein|automat|lexic|tokeniz|dictionar|vocabular"),
    ("security", r"security|exploit|vulnerab|crypt|malware|fuzz|\battacks?\b|mitigat|"
                 r"blackhat|defcon|reverse engineer|shellcode|\bctf\b|sandbox|"
                 r"address space layout|\baslr\b|\bcfi\b|\bpoc\b|gtfo|sanitiz|anti-debug|"
                 r"obfusc|rootkit|trojan|\bvirus\b|backdoor|password|authenticat|"
                 r"\btls\b|\bssl\b|overflow|forensic|meltdown|spectre|kaslr|injection|"
                 r"\bhooks?\b|syscan|\bida pro\b|hex-?rays"),
    ("memory", r"memory|\bcach(e|es|ing)\b|\btlb\b|\bnuma\b|alloc|paging|\bdram\b|\bheap\b|"
               r"persistent memory|\bnvm\b|pointer"),
    ("storage-io", r"file ?system|\bdisks?\b|\bssds?\b|\bnvme\b|storage|\braid\b|\bio\b|"
                   r"i\/o|\bscsi\b|flash|block layer|\bzfs\b|io_uring|iocp"),
    ("distributed", r"distribut|consensus|\braft\b|paxos|replicat|byzantine|cluster|"
                    r"\bcap theorem\b|eventually consistent|sharding|zookeeper|spanner|"
                    r"message.passing|rollback.recovery|fault.?toler|\bmapreduce\b|"
                    r"\bhadoop\b|\bspark\b|datacenter|warehouse-scale|\bray\b"),
    ("formal-methods", r"formal (method|verif|spec)|verification|model check|theorem prov|"
                       r"\bproofs?\b|tla\+|pluscal|\bsmt\b|\bz3\b|\bcoq\b|dijkstra|"
                       r"\bewd\d*\b|hoare|invariant|refinement|sat solver"),
    ("software-eng", r"software (engineer|quality|assert|develop)|test driven|\btdd\b|"
                     r"waterfall|agile|code (review|quality)|empirical|refactor|"
                     r"design pattern|object-?oriented|oopsla|proactor|reactor|"
                     r"anti-?pattern|technical debt|maintain|productiv"),
    ("dev-tools", r"\bgit\b|\bgdb\b|\bvim\b|emacs|doxygen|sphinx|pandoc|latex|\btex\b|"
                  r"\bbash\b|powershell|makefile|build system|jupyter|notebook|"
                  r"cheat ?sheet|quick reference|reference card|markdown|\bcli\b"),
    ("data-science", r"\bpandas\b|numpy|scipy|matplotlib|\bcudf\b|rapids|data (science|"
                     r"mining|stream)|analytics|visualiz|treemap|\bcharts?\b|plot|"
                     r"r packages?|r extensions|\bcran\b|dataframe|outlier|pagerank|"
                     r"frequent items|\btime series\b"),
    ("history-retro", r"history|\bdec\b|jovial|oral histor|retrospective|obituar|"
                      r"\bsega\b|playstation|nintendo|atari|commodore|amiga|\bcray\b|"
                      r"computer history museum|folklore|downfall"),
]
TOPICS = [name for name, _ in TOPIC_RULES] + ["misc"]
_COMPILED_RULES = [(name, re.compile(pat, re.I)) for name, pat in TOPIC_RULES]

TYPE_SLIDES_RE = re.compile(
    r"(?<![a-z])(slides?|presentation|keynote|webcast)(?![a-z])"
    r"|\b(gdc|gtc)\b", re.I)
TYPE_THESIS_RE = re.compile(r"\b(thesis|dissertation)\b", re.I)
TYPE_BOOK_RE = re.compile(r"\b(edition|textbook|book)\b", re.I)
TYPE_MANUAL_RE = re.compile(
    r"\b(manuals?|reference|programm(er|ing)('|’)?s? guide|user('|’)?s? guide|"
    r"handbook|datasheet|data sheet|specification|working draft|documentation|"
    r"developer('|’)?s guide|cheat sheet|quick guide)\b", re.I)
TYPE_REPORT_RE = re.compile(r"\b(white ?paper|technical report|tech report|tr-\d+)\b", re.I)


def strip_ext(basename):
    """Return (stem, ext, noext).  Strips stacked extensions ('x.pdf.docx');
    the OUTERMOST extension is the real format.  Files with no recognized
    extension and no dot at all are assumed to be PDFs (two such exist)."""
    stem, exts = basename, []
    while True:
        m = EXT_RE.search(stem)
        if not m:
            break
        exts.append(m.group(1).lower())
        stem = stem[: m.start()]
    if exts:
        return stem, exts[0], False
    if "." not in basename:
        return basename, "pdf", True
    return None, None, False  # unrecognized extension -> excluded


def is_dateish(s):
    """True iff s is purely a date: exactly one plausible year plus only
    month names, day numbers/ordinals, commas, hyphens.  '(August 2016)',
    '(2013-09-04)', '(1999, Jan)', '(May 10th, 1994)' yes; '(EWD426)' no."""
    years = YEAR_RE.findall(s)
    if len(years) != 1:
        return False
    rest = YEAR_RE.sub(" ", s)
    for tok in re.findall(r"[A-Za-z]+|\d+", rest):
        t = tok.lower()
        if t in MONTHS or t in ("st", "nd", "rd", "th"):
            continue
        if re.fullmatch(r"\d{1,2}", t):
            continue
        return False
    return True


def year_from_arxiv(arxiv_id):
    yy, mm = int(arxiv_id[:2]), int(arxiv_id[2:4])
    if 1 <= mm <= 12 and 7 <= yy <= 26:  # new-style arXiv ids began 2007-04
        return 2000 + yy
    return None


def parse_stem(stem):
    """Parse a filename stem per the grammar.  Returns dict with
    title, quals (display string), year, id, arxiv."""
    full_stem = stem
    year = None
    pid = None
    arxiv = False

    # Pop trailing parentheticals right-to-left.  First opaque group is the
    # id; a second one stays in the title (the CHM92 case).  Date-ish groups
    # contribute the year and are always consumed.
    while True:
        m = TRAIL_RE.search(stem)
        if not m:
            break
        g = m.group(1).strip()
        if ARXIV_RE.fullmatch(g):
            if pid:
                break
            pid, arxiv = g, True
        elif is_dateish(g):
            if year is None:
                m2 = YEAR_RE.search(g)
                year = int(m2.group(0))
        elif pid:
            break
        else:
            pid = g
        stem = stem[: m.start()]

    fields = [f.strip() for f in stem.split(" - ") if f.strip()]
    quals = []
    if fields and re.fullmatch(r"(19|20)\d{2}", fields[0]):
        # Leading-year grammar (Perfect Hashing/): everything after the year
        # is the full title.
        if year is None:
            year = int(fields[0])
        title = " - ".join(fields[1:]) if len(fields) > 1 else fields[0]
    elif fields:
        title = fields[0]
        for q in fields[1:]:
            if is_dateish(q):
                if year is None:
                    year = int(YEAR_RE.search(q).group(0))
            elif q.lower() != "slides":
                quals.append(q)
    else:
        title = full_stem

    if year is None and arxiv:
        year = year_from_arxiv(pid)
    if year is None:
        matches = YEAR_RE.findall(full_stem)
        if matches:
            year = int(matches[-1])
    if year is not None and not (1900 <= year <= 2026):
        year = None

    return {
        "title": title,
        "quals": " · ".join(quals),
        "year": year,
        "id": pid,
        "arxiv": arxiv,
    }


def classify_type(stem, ext):
    if ext in ("ppt", "pptx"):
        return "slides"
    if ext == "xlsx":
        return "data"
    if ext == "txt":
        return "text"
    if TYPE_SLIDES_RE.search(stem):
        return "slides"
    if TYPE_THESIS_RE.search(stem):
        return "thesis"
    if TYPE_BOOK_RE.search(stem):
        return "book"
    if TYPE_MANUAL_RE.search(stem):
        return "manual"
    if TYPE_REPORT_RE.search(stem):
        return "report"
    return "paper"


def topics_for(path):
    found = [name for name, rx in _COMPILED_RULES if rx.search(path)]
    if path.startswith("Perfect Hashing/") and "hashing" not in found:
        found.insert(0, "hashing")
    return found or ["misc"]


def included(path):
    top = path.split("/", 1)[0]
    if top in EXCLUDE_TOP or top.endswith("_files"):
        return False
    if path in EXCLUDE_FILES:
        return False
    return True


def iter_tree_local(repo):
    out = subprocess.run(
        ["git", "-C", repo, "-c", "core.quotePath=false", "ls-tree", "-r",
         "-z", "--format=%(objectname)%x09%(objectsize)%x09%(path)", "HEAD"],
        check=True, capture_output=True).stdout.decode("utf-8")
    for rec in out.split("\0"):
        if rec:
            sha, size, path = rec.split("\t", 2)
            yield sha, int(size), path


def iter_tree_api(tree_json_path):
    with open(tree_json_path, encoding="utf-8") as f:
        tree = json.load(f)
    if tree.get("truncated"):
        sys.exit("FATAL: Trees API response is truncated; cannot build a complete manifest.")
    for e in tree["tree"]:
        if e["type"] == "blob":
            yield e["sha"], e["size"], e["path"]


def head_commit(repo):
    return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          check=True, capture_output=True).stdout.decode().strip()


def build_manifest(entries, commit):
    files = []
    total_bytes = 0
    for sha, size, path in sorted(entries, key=lambda e: e[2].lower()):
        if not included(path):
            continue
        stem, ext, noext = strip_ext(path.rsplit("/", 1)[-1])
        if stem is None:
            continue
        parsed = parse_stem(stem)
        ftype = classify_type(stem, ext)
        rec = {
            "p": path,
            "t": parsed["title"],
            "q": parsed["quals"],
            "y": parsed["year"],
            "i": parsed["id"],
            "a": 1 if parsed["arxiv"] else 0,
            "k": TYPES.index(ftype),
            "c": [TOPICS.index(t) for t in topics_for(path)],
            "s": size,
            "e": ext,
            "h": sha[:12],
        }
        if noext:
            rec["x"] = 1  # no real extension on disk; treat as PDF
        files.append(rec)
        total_bytes += size
    if not files:
        sys.exit("FATAL: zero files matched; refusing to emit an empty manifest.")
    return {
        "v": 1,
        "generated": datetime.datetime.now(datetime.timezone.utc)
                     .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": commit,
        "count": len(files),
        "bytes": total_bytes,
        "types": TYPES,
        "topics": TOPICS,
        "files": files,
    }


def report(manifest):
    files = manifest["files"]
    n = len(files)
    print(f"{n} files, {manifest['bytes'] / 1e9:.2f} GB")
    no_year = [f for f in files if f["y"] is None]
    print(f"  with year: {n - len(no_year)}  without: {len(no_year)}")
    print(f"  with id: {sum(1 for f in files if f['i'])}  "
          f"arxiv: {sum(1 for f in files if f['a'])}")
    print("types:")
    for i, name in enumerate(manifest["types"]):
        c = sum(1 for f in files if f["k"] == i)
        if c:
            print(f"  {name:8} {c}")
    print("topics:")
    for i, name in enumerate(manifest["topics"]):
        c = sum(1 for f in files if i in f["c"])
        print(f"  {name:16} {c}")
    misc_idx = manifest["topics"].index("misc")
    misc = [f["p"] for f in files if misc_idx in f["c"]]
    print(f"misc sample ({len(misc)} total):")
    for p in misc[:30]:
        print(f"  {p}")


def assemble(manifest, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name in SITE_FILES:
        shutil.copy2(os.path.join(SCRIPT_DIR, name), os.path.join(out_dir, name))
    write_json(manifest, os.path.join(out_dir, "manifest.json"))


def write_json(manifest, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tree-json", help="GitHub Trees API response (?recursive=1); "
                                        "if omitted, read the local git tree")
    ap.add_argument("--repo", default=os.path.dirname(SCRIPT_DIR),
                    help="repo working copy (local mode)")
    ap.add_argument("--commit", help="commit sha to stamp into the manifest "
                                     "(defaults to local HEAD)")
    ap.add_argument("--out", help="write manifest.json here")
    ap.add_argument("--assemble", metavar="DIR",
                    help="assemble the full site (html/js/css + manifest) into DIR")
    ap.add_argument("--report", action="store_true", help="print parse-quality stats")
    args = ap.parse_args()

    if args.tree_json:
        entries = list(iter_tree_api(args.tree_json))
        commit = args.commit or "unknown"
    else:
        entries = list(iter_tree_local(args.repo))
        commit = args.commit or head_commit(args.repo)

    manifest = build_manifest(entries, commit)
    print(f"manifest: {manifest['count']} files, "
          f"{manifest['bytes'] / 1e9:.2f} GB, commit {commit[:12]}", file=sys.stderr)

    if args.report:
        report(manifest)
    if args.out:
        write_json(manifest, args.out)
    if args.assemble:
        assemble(manifest, args.assemble)


if __name__ == "__main__":
    main()
