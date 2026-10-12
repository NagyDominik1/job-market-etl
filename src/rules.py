"""Free, rule-based job classification (no API key needed).

Used by src/enrich.py for every job (Claude later upgrades some of them). It
gives the same fields as Claude, using keyword matching:
    seniority          keywords in the title, e.g. "Senior", "Werkstudent", "Gyakornok";
                       if the title has none, the years of experience decide
    category           keywords in the title, e.g. "Frontend", "Sales"
    skills             a curated list of technologies found in the title + description
    years_required     e.g. "3+ years", "3+ év", "najmanje 3 godine" -> 3
    required_languages e.g. "fluent German", or a posting written in German -> ["german"]

Titles and texts are "folded" first (src/normalize.py: lowercase, Serbian
Cyrillic -> Latin, no accents), so "Gyakornok", "Стажиста" and "stažista"
all match the plain-ASCII keywords below.

It is fast and free, but much less accurate than Claude: it only knows the
words in these lists and can't understand context.
"""

import re

from src.normalize import fold

# Stored in job_enrichment.model for rows produced by these rules.
RULE_BASED_MODEL = "rule-based"


# --- Skills -------------------------------------------------------------------
# Most skills are matched as a whole word, ignoring upper/lower case. "Whole
# word" here means the characters right before and after must not be a letter,
# digit, "+", "#" or "." (before), so "Java" does not match inside "JavaScript",
# "SQL" not inside "PostgreSQL", and "C" not inside "C++".
WORD_BEFORE = r"(?<![\w+#.])"
WORD_AFTER = r"(?![\w+#])"


def word(*names: str) -> str:
    """Regex for one or more spellings of a skill, each as a whole word."""
    alternatives = "|".join(re.escape(name) for name in names)
    return WORD_BEFORE + "(?:" + alternatives + ")" + WORD_AFTER


# Short or ambiguous names only count when they appear in a list of
# technologies, e.g. "Python, Go and SQL" or "(R/Python)". That way the English
# verb "go" ("go to market") or a single "R" is not counted as a language.
# Python's lookbehind needs a fixed length, so we check "[,/(]" and "[,/(] " separately.
IN_A_LIST_BEFORE = r"(?:(?<=[,/(|])|(?<=[,/(|] ))"
IN_A_LIST_AFTER = r"(?=\s?[,/)|])"


def listed(name: str) -> str:
    """Regex for a short, CASE-SENSITIVE name that must appear in a list."""
    escaped = re.escape(name)
    return (
        IN_A_LIST_BEFORE + escaped + WORD_AFTER
        + "|" + WORD_BEFORE + escaped + IN_A_LIST_AFTER
    )


# (canonical name, regex, case_sensitive)
# case_sensitive=True is used for names that are also normal words in lower
# case, e.g. "react" (verb), "spark", "excel" (verb), "rest" (German "Rest").
SKILL_PATTERNS = [
    # Programming languages
    ("Python", word("Python"), False),
    ("Java", word("Java"), False),
    ("JavaScript", word("JavaScript"), False),
    ("TypeScript", word("TypeScript"), False),
    # (?i:...) makes only "golang" case-insensitive; "Go" itself must be capitalised.
    ("Go", "(?i:" + word("Golang") + ")|" + listed("Go"), True),
    ("R", listed("R") + "|" + word("RStudio"), True),
    ("C++", WORD_BEFORE + r"C\+\+" + r"(?![\w+])", False),
    ("C#", WORD_BEFORE + r"C#" + r"(?!\w)", False),
    # ".NET" must be upper case, so web addresses like "example.net" don't match.
    (".NET", r"(?<![\w.])(?:ASP)?\.NET(?!\w)|" + word("dotnet"), True),
    ("PHP", word("PHP"), False),
    ("Ruby", word("Ruby", "Ruby on Rails"), False),
    ("Rust", word("Rust"), True),
    ("Scala", word("Scala"), False),
    ("Kotlin", word("Kotlin"), False),
    ("Swift", word("Swift"), True),
    ("SQL", word("SQL"), False),
    # Frontend
    ("HTML", word("HTML", "HTML5"), False),
    ("CSS", word("CSS", "CSS3"), False),
    ("React", word("React", "React.js", "ReactJS"), True),
    ("Angular", word("Angular"), True),
    ("Vue.js", word("Vue", "Vue.js", "VueJS"), False),
    ("Next.js", word("Next.js", "NextJS"), False),
    ("Node.js", word("Node.js", "NodeJS"), False),
    # Backend frameworks and APIs
    ("Django", word("Django"), False),
    ("Flask", word("Flask"), True),
    ("FastAPI", word("FastAPI"), False),
    ("Spring Boot", word("Spring Boot"), False),
    ("Laravel", word("Laravel"), False),
    ("GraphQL", word("GraphQL"), False),
    ("REST", word("REST", "RESTful"), True),
    # Mobile
    ("iOS", word("iOS"), False),
    ("Android", word("Android"), False),
    ("Flutter", word("Flutter"), False),
    # Databases
    ("PostgreSQL", word("PostgreSQL", "Postgres"), False),
    ("MySQL", word("MySQL"), False),
    ("MongoDB", word("MongoDB"), False),
    ("Redis", word("Redis"), False),
    ("Elasticsearch", word("Elasticsearch"), False),
    ("Snowflake", word("Snowflake"), True),
    # Data and ML
    ("Spark", word("Apache Spark", "PySpark", "Spark"), True),
    ("Kafka", word("Kafka"), False),
    ("Airflow", word("Airflow", "Apache Airflow"), True),
    ("dbt", word("dbt"), True),
    ("pandas", word("pandas"), False),
    ("TensorFlow", word("TensorFlow"), False),
    ("PyTorch", word("PyTorch"), False),
    ("scikit-learn", word("scikit-learn", "sklearn"), False),
    ("LLM", word("LLM", "LLMs"), False),
    ("Power BI", word("Power BI", "PowerBI"), False),
    ("Tableau", word("Tableau"), True),
    ("Excel", word("Excel", "MS Excel"), True),
    # Cloud and DevOps
    ("AWS", word("AWS", "Amazon Web Services"), False),
    ("Azure", word("Azure"), True),
    ("GCP", word("GCP", "Google Cloud"), False),
    ("Docker", word("Docker"), False),
    ("Kubernetes", word("Kubernetes", "K8s"), False),
    ("Terraform", word("Terraform"), False),
    ("Ansible", word("Ansible"), False),
    ("Linux", word("Linux"), False),
    ("Git", word("Git"), False),
    ("CI/CD", word("CI/CD"), False),
    # Business tools
    ("SAP", word("SAP"), True),
    ("Salesforce", word("Salesforce"), False),
    ("HubSpot", word("HubSpot"), False),
    ("Jira", word("Jira"), False),
    ("Figma", word("Figma"), False),
    # Game development ("unity" in lower case is a normal word: case-sensitive)
    ("Unity", word("Unity", "Unity3D"), True),
]

# Compile each regex once, when the module is imported (faster than every call).
COMPILED_SKILLS = [
    (name, re.compile(pattern, 0 if case_sensitive else re.IGNORECASE))
    for name, pattern, case_sensitive in SKILL_PATTERNS
]


def find_skills(title: str, description: str | None) -> list[str]:
    """Return the known skills mentioned in the title or description.

    Skills are ordered by where they first appear (title first), as a simple
    guess for "most important first".
    """
    text = f"{title}\n{description or ''}"
    found = []  # (position of first match, skill name)
    for name, regex in COMPILED_SKILLS:
        match = regex.search(text)
        if match:
            found.append((match.start(), name))
    found.sort()  # sorts by position
    return [name for _, name in found]


# --- Seniority ------------------------------------------------------------------
# Checked in this order; the first level with a matching keyword wins.
# (intern before lead: "Team Lead Intern" is an internship.)
SENIORITY_RULES = [
    # Hungarian gyakornok; Serbian praksa/praksi, praktikant (covered by prakti...), stažista.
    ("intern", r"\bintern(ship)?\b|\bwerkstudent|\bworking student\b|\bprakti(kum|kant)"
               r"|\btrainee\b|\bapprentice|\bausbildung\b|\bazubi\b|\bstage\b|\bstagiaire\b|\balternance\b"
               r"|\bgyakornok|\bpraks[aeiu]\b|\bstazist"),
    # "lead" but not "Lead Generation"; "Staff" only at the start ("Staff Engineer",
    # not "Member of Marketing Staff"); German "Leiter"/"Leitung" also inside words.
    # Hungarian vezető (also at the end of "csoportvezető"), Serbian rukovodilac.
    ("lead", r"\blead\b(?!\s*gen)|\bhead of\b|\bprincipal\b|\bdirector\b|\bvp\b|\bchief\b"
             r"|^\(?staff\b|leiter\b|leitung\b|vezeto\b|\brukovodi"),
    ("senior", r"\bsenior\b|\bsr\.?(?=\s)"),
    # Hungarian pályakezdő (career starter), Serbian početnik/početni.
    ("junior", r"\bjunior\b|\bjr\.?(?=\s)|\bgraduate\b|\bentry[- ]level\b|\bberufseinsteiger"
               r"|\bpalyakezdo|\bpocetni"),
    # Not just "mid", which also appears in "Mid-Market Account Executive".
    # "medior" is used in Hungarian and Serbian too.
    ("mid", r"\bmid[- ]?level\b|\bmedior\b|\bintermediate\b"),
]


def classify_seniority(title: str) -> str:
    """Guess the seniority level from keywords in the title."""
    folded = fold(title)
    for level, pattern in SENIORITY_RULES:
        if re.search(pattern, folded, re.IGNORECASE):
            return level
    return "unknown"


def seniority_from_years(years: int) -> str:
    """Seniority when the title says nothing: 0-2 years junior, 3-4 mid, 5+ senior."""
    if years <= 2:
        return "junior"
    if years <= 4:
        return "mid"
    return "senior"


# --- Years of experience ---------------------------------------------------------
# Matched on the folded text (év -> ev, godinе -> godine, Jahre -> jahre).
YEAR_WORD = r"(?:years?|yrs?|ev(?:e?s|et|nyi)?|godin[aeu]?|god\.|jahre?n?)"
EXPERIENCE_WORD = r"(?:experience|tapasztalat|iskustv|berufserfahrung|erfahrung)"
# Not the END of a range: in "2-3 years" or "2 to 3 years" only the 2 counts
# (the range pattern below reads it), so "3" must not be read on its own.
NOT_RANGE_END = r"(?<![-–])(?<![-–] )(?<!to )(?<!do )(?<!bis )"
YEARS_PATTERNS = [re.compile(pattern) for pattern in (
    # "min. 3 years", "at least 3 years", "legalább 3 év", "najmanje 3 godine", "mindestens 3 Jahre"
    r"(?:\bmin(?:imum)?\.?|\bat least|\blegalabb|\bminimalno|\bnajmanje|\bmindestens)\s*(\d{1,2})\s*\+?\s*" + YEAR_WORD + r"\b",
    # "3+ years", "3+ év", "3+ godine", "3+ Jahre"
    NOT_RANGE_END + r"\b(\d{1,2})\s*\+\s*" + YEAR_WORD + r"\b",
    # "3-5 years", "3 to 5 years", "3–5 év", "3 do 5 godina" -> the lower bound
    r"\b(\d{1,2})\s*(?:-|–|to|do|bis)\s*\d{1,2}\s*" + YEAR_WORD + r"\b",
    # "3 years of experience", "3 év szakmai tapasztalat", "3 godine radnog iskustva"
    NOT_RANGE_END + r"\b(\d{1,2})\s*" + YEAR_WORD + r"\s+(?:of\s+)?(?:[a-z]+\s+){0,3}?" + EXPERIENCE_WORD,
)]

# More than this is not an experience requirement ("18+ years" is an age limit).
MAX_PLAUSIBLE_YEARS = 15


def detect_years_required(text: str | None) -> int | None:
    """Return the years of experience a posting asks for, or None if it doesn't say.

    If several numbers appear ("2+ years of Python, 5+ years overall"), the
    largest one is the real requirement.
    """
    folded = fold(text)
    found = [
        int(match.group(1))
        for pattern in YEARS_PATTERNS
        for match in pattern.finditer(folded)
    ]
    found = [years for years in found if years <= MAX_PLAUSIBLE_YEARS]
    return max(found) if found else None


# --- Required spoken languages -----------------------------------------------------
# Only languages that can be dealbreakers are detected (Hungarian, Serbian and
# English postings are fine for me). Names in English, German, Hungarian,
# Serbian and French, folded: "németül" -> "nemetul", "немачки" -> "nemacki".
# The endings are spelled out so country names don't match: "Germany",
# "Deutschland", "Németország", "Nemačka" (Germany) are places, not languages.
LANGUAGE_WORDS = {
    # "deutsch" only as the language ("Deutsch", "Deutschkenntnisse"), not
    # "Deutsche Telekom" / "Deutsche Bahn".
    "german": r"\b(?:german\b|deutsch(?:kenntnisse|sprachig\w*|niveau)?\b|nemet(?:ul|\b)"
              r"|n(?:j)?emack(?:i|og|om)\b|allemand)",
    "french": r"\b(?:french\b|francais|franzosisch\w*|francia(?:ul|\b)|francusk(?:i|og|om)\b)",
}
# Words that make a mention a REQUIREMENT ("fluent German", "Deutschkenntnisse C1").
REQUIRED_CUES = re.compile(
    r"fluen|native|business[- ](?:level|fluent|fluency|proficien)|proficien|excellent|very good|strong"
    r"|good command|written and spoken"
    r"|required|must|mandatory|\b[bc][12]\b|kenntnisse|fliessend|verhandlungssicher|sehr gut"
    r"|targyalokepes|nyelvtudas|nyelvismeret|szintu|tecno|znanje|poznavanje|jezik"
)
# Words that make it optional ("German is a plus", "Deutsch von Vorteil").
OPTIONAL_CUES = re.compile(
    r"\bplus\b|nice to have|advantage|asset|preferred|beneficial|bonus|von vorteil|wunschenswert"
    r"|would be|\bideal|helpful|desirable"
    r"|elony|prednost|pozeljn"
)
# Very common words of a language: a description full of them is WRITTEN in that
# language, so it is required even if it never says so.
STOPWORDS = {
    "german": {"und", "der", "die", "das", "mit", "fur", "wir", "sie", "ist", "ein", "eine",
               "zu", "von", "bei", "auf", "den", "dem", "unser", "unsere", "ihre", "deine"},
    "french": {"et", "le", "la", "les", "des", "nous", "vous", "pour", "une", "avec", "dans",
               "est", "du", "au", "votre", "notre"},
}
WRITTEN_IN_SHARE = 0.10   # at least 10% of the words are that language's stopwords
WRITTEN_IN_MIN_WORDS = 40 # too short a text says nothing


def written_in(folded: str, language: str) -> bool:
    words = re.findall(r"[a-z]+", folded)
    if len(words) < WRITTEN_IN_MIN_WORDS:
        return False
    share = sum(word in STOPWORDS[language] for word in words) / len(words)
    return share >= WRITTEN_IN_SHARE


def detect_required_languages(text: str | None) -> list[str]:
    """Return the spoken languages (of LANGUAGE_WORDS) the posting requires, sorted."""
    folded = fold(text)
    required = set()
    # Check each sentence (or line / bullet point) on its own, so a cue in
    # another sentence doesn't count.
    sentences = re.split(r"[.!?;\n•]+", folded)
    for language, pattern in LANGUAGE_WORDS.items():
        if written_in(folded, language):
            required.add(language)
            continue
        for sentence in sentences:
            if (re.search(pattern, sentence) and REQUIRED_CUES.search(sentence)
                    and not OPTIONAL_CUES.search(sentence)):
                required.add(language)
                break
    return sorted(required)


# --- Category ---------------------------------------------------------------------
# Checked in this order; the first category with a matching keyword wins. The
# specific technical categories come first, so "Data Security Engineer" is
# "security" and "Marketing Data Analyst" is "data", not "non_tech".
CATEGORY_RULES = [
    ("security", r"\bsecurity\b|\bcyber|\bpentest|\binfosec\b|\bsicherheit|\bsoc analyst"),
    ("ai_ml", r"\bmachine learning\b|\bml\b|\bai\b|\bki\b|\bartificial intelligence\b"
              r"|\bdeep learning\b|\bnlp\b|\bcomputer vision\b|\bllms?\b"),
    # Hungarian adat- (adatmérnök, adatelemző), Serbian podaci/podataka.
    ("data", r"\bdata\b|\bdaten|\badat|\bpodat|\banalytics\b|\bbi\b|\bbusiness intelligence\b|\betl\b|\bdatabase\b|\bdatenbank"),
    ("devops", r"\bdevops\b|\bsre\b|\bsite reliability\b|\bplatform engineer|\bcloud\b|\binfrastru"
               r"|\bsysadmin\b|\bsystem ?administrator|\bsystemadministrator|\bnetwork engineer|\bnetzwerk"
               r"|\breseaux\b"),
    ("mobile", r"\bmobile\b|\bios\b|\bandroid\b|\bflutter\b|\breact native\b|\bapp[- ]?(developer|entwickler)"),
    ("qa", r"\bqa\b|\bquality assurance\b|\btest engineer|\btester\b|\btest automation|\btestautomatisierung|\bsdet\b"),
    ("design", r"\bdesigner\b|\bux\b|\bui\b|\bproduct design|\bgrafik|\bgraphic"),
    ("fullstack", r"\bfull[- ]?stack\b"),
    ("frontend", r"\bfront[- ]?end\b"),
    ("backend", r"\bback[- ]?end\b"),
    # Some German words are matched without a leading \b, because they often
    # appear at the END of a compound word ("Staplerfahrer" = forklift driver).
    ("non_tech", r"\bsales\b|\baccount (executive|manager|development|director)|\bbusiness development"
                 r"|\bcommercial|\bmarketing|\bmarketer\b|\bhr\b|\bhuman resources\b|\bressources humaines\b|\brecruit|\btalent\b"
                 r"|\bpeople (partner|operations)|\bfinance|\bfinanz|\baccountant\b|\baccounting\b|\bcomptab"
                 r"|\bbuchhalt|\bsteuer|\bcontroll(er|ing)\b|\bpayroll\b|\blohn"
                 r"|\bcustomer (success|support|experience|service|care)|\bsupport agent\b|\bkundenservice"
                 r"|\bvertrieb|\bverkauf|\beinkauf|\bprocurement\b|\blogisti|\bsupply chain\b|\blager"
                 r"|\bwarehouse\b|fahrer\b|\bdriver\b|\boperations\b|\boffice\b|\bassistant|\bassistenz"
                 r"|\blegal\b|\bcounsel\b|\bjurist|\blawyer\b|\brechtsanwalt|\bwaiter|\bwaitress|\bkellner"
                 r"|\bpflege|\bnurse\b|\berzieher|\bteacher\b|\blehrer|\bretail\b|\bpartnerships?\b"
                 r"|\balliances\b|\bcommunications\b|\bcontent\b|\bevents?\b|\bgtm\b|\brevenue\b"),
]


def classify_category(title: str) -> str:
    """Guess the job category from keywords in the title."""
    folded = fold(title)
    for category, pattern in CATEGORY_RULES:
        if re.search(pattern, folded, re.IGNORECASE):
            return category
    return "other"


def classify_with_rules(title: str, description: str | None) -> dict:
    """Return all enrichment fields, using only the rules above."""
    text = f"{title}\n{description or ''}"
    years = detect_years_required(text)
    seniority = classify_seniority(title)
    if seniority == "unknown" and years is not None:
        seniority = seniority_from_years(years)
    return {
        "seniority": seniority,
        "category": classify_category(title),
        "skills": find_skills(title, description),
        "years_required": years,
        "required_languages": detect_required_languages(text),
    }
