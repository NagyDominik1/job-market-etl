"""Free, rule-based job classification (no API key needed).

Used by src/enrich.py when ANTHROPIC_API_KEY is empty. It gives the same three
fields as Claude, using keyword matching:
    seniority - keywords in the title, e.g. "Senior", "Werkstudent"
    category  - keywords in the title, e.g. "Frontend", "Sales"
    skills    - a curated list of technologies found in the title + description

It is fast and free, but much less accurate than Claude: it only knows the
words in these lists and can't understand context.
"""

import re

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
    ("intern", r"\bintern(ship)?\b|\bwerkstudent|\bworking student\b|\bprakti(kum|kant)"
               r"|\btrainee\b|\bapprentice|\bausbildung\b|\bazubi\b|\bstage\b|\bstagiaire\b|\balternance\b"),
    # "lead" but not "Lead Generation"; "Staff" only at the start ("Staff Engineer",
    # not "Member of Marketing Staff"); German "Leiter"/"Leitung" also inside words.
    ("lead", r"\blead\b(?!\s*gen)|\bhead of\b|\bprincipal\b|\bdirector\b|\bvp\b|\bchief\b"
             r"|^\(?staff\b|leiter\b|leitung\b"),
    ("senior", r"\bsenior\b|\bsr\.?(?=\s)"),
    ("junior", r"\bjunior\b|\bjr\.?(?=\s)|\bgraduate\b|\bentry[- ]level\b|\bberufseinsteiger"),
    # Not just "mid", which also appears in "Mid-Market Account Executive".
    ("mid", r"\bmid[- ]?level\b|\bmedior\b|\bintermediate\b"),
]


def classify_seniority(title: str) -> str:
    """Guess the seniority level from keywords in the title."""
    for level, pattern in SENIORITY_RULES:
        if re.search(pattern, title, re.IGNORECASE):
            return level
    return "unknown"


# --- Category ---------------------------------------------------------------------
# Checked in this order; the first category with a matching keyword wins. The
# specific technical categories come first, so "Data Security Engineer" is
# "security" and "Marketing Data Analyst" is "data", not "non_tech".
CATEGORY_RULES = [
    ("security", r"\bsecurity\b|\bcyber|\bpentest|\binfosec\b|\bsicherheit|\bsoc analyst"),
    ("ai_ml", r"\bmachine learning\b|\bml\b|\bai\b|\bki\b|\bartificial intelligence\b"
              r"|\bdeep learning\b|\bnlp\b|\bcomputer vision\b|\bllms?\b"),
    ("data", r"\bdata\b|\bdaten|\banalytics\b|\bbi\b|\bbusiness intelligence\b|\betl\b|\bdatabase\b|\bdatenbank"),
    ("devops", r"\bdevops\b|\bsre\b|\bsite reliability\b|\bplatform engineer|\bcloud\b|\binfrastru"
               r"|\bsysadmin\b|\bsystem ?administrator|\bsystemadministrator|\bnetwork engineer|\bnetzwerk"
               r"|\bréseaux\b"),
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
    for category, pattern in CATEGORY_RULES:
        if re.search(pattern, title, re.IGNORECASE):
            return category
    return "other"


def classify_with_rules(title: str, description: str | None) -> dict:
    """Return {"seniority", "category", "skills"} using only the rules above."""
    return {
        "seniority": classify_seniority(title),
        "category": classify_category(title),
        "skills": find_skills(title, description),
    }
