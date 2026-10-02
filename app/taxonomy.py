"""Canonical skill taxonomy.

A resume can say "js", "JavaScript", "Java Script" or "ES6" and mean one thing.
Counting raw strings gives you a chart with four bars where there should be one,
so every mention is resolved to a canonical name and a category before it is stored.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

CATEGORIES = {
    "lang": "Programming languages",
    "front": "Frontend",
    "back": "Backend & APIs",
    "data": "Data, ML & AI",
    "cloud": "Cloud & DevOps",
    "db": "Databases",
    "test": "Testing & QA",
    "tool": "Tooling & practice",
    "soft": "Professional skills",
}

# canonical name -> (category, [aliases])
TAXONOMY: Dict[str, Tuple[str, List[str]]] = {
    "Python": ("lang", ["python3", "py"]),
    "Java": ("lang", []),
    "JavaScript": ("lang", ["js", "es6", "ecmascript", "java script"]),
    "TypeScript": ("lang", ["ts"]),
    "C++": ("lang", ["cpp", "c plus plus"]),
    "C": ("lang", []),
    "C#": ("lang", ["csharp", "c sharp"]),
    "Go": ("lang", ["golang"]),
    "Rust": ("lang", []),
    "Ruby": ("lang", []),
    "PHP": ("lang", []),
    "Kotlin": ("lang", []),
    "Swift": ("lang", []),
    "Scala": ("lang", []),
    "R": ("lang", []),
    "MATLAB": ("lang", []),
    "SQL": ("lang", ["pl/sql", "plsql", "t-sql", "tsql"]),
    "Shell scripting": ("lang", ["bash", "shell", "zsh", "powershell"]),
    "HTML": ("front", ["html5"]),
    "CSS": ("front", ["css3", "scss", "sass", "less"]),
    "React": ("front", ["react.js", "reactjs", "react js", "react native"]),
    "Angular": ("front", ["angularjs", "angular.js"]),
    "Vue": ("front", ["vue.js", "vuejs"]),
    "Next.js": ("front", ["nextjs", "next js"]),
    "Redux": ("front", []),
    "Tailwind CSS": ("front", ["tailwind", "tailwindcss"]),
    "Bootstrap": ("front", []),
    "jQuery": ("front", []),
    "Figma": ("front", []),
    "Node.js": ("back", ["nodejs", "node js", "node"]),
    "Express": ("back", ["express.js", "expressjs"]),
    "Django": ("back", []),
    "Flask": ("back", []),
    "FastAPI": ("back", ["fast api"]),
    "Spring Boot": ("back", ["springboot", "spring", "spring mvc"]),
    "REST APIs": ("back", ["rest", "restful", "rest api", "restful apis", "restful api"]),
    "GraphQL": ("back", ["graph ql"]),
    "gRPC": ("back", []),
    "Microservices": ("back", ["micro services", "micro-services"]),
    "Kafka": ("back", ["apache kafka"]),
    "RabbitMQ": ("back", []),
    "Celery": ("back", []),
    ".NET": ("back", ["asp.net", "dotnet", ".net core"]),
    "Machine learning": ("data", ["ml", "machine-learning"]),
    "Deep learning": ("data", ["dl", "deep-learning"]),
    "NLP": ("data", ["natural language processing", "text mining"]),
    "Computer vision": ("data", ["opencv", "image processing"]),
    "TensorFlow": ("data", ["tensor flow", "keras"]),
    "PyTorch": ("data", ["torch"]),
    "scikit-learn": ("data", ["sklearn", "scikit learn"]),
    "Pandas": ("data", []),
    "NumPy": ("data", []),
    "LLMs": ("data", ["large language models", "llm", "openai api", "prompt engineering", "transformers"]),
    "LangChain": ("data", ["lang chain"]),
    "Vector databases": ("data", ["pinecone", "faiss", "chromadb", "embeddings", "rag"]),
    "Statistics": ("data", ["statistical analysis", "statistical modelling", "statistical modeling"]),
    "Data visualisation": ("data", ["data visualization", "matplotlib", "seaborn", "plotly", "d3.js"]),
    "Power BI": ("data", ["powerbi", "power-bi"]),
    "Tableau": ("data", []),
    "Excel": ("data", ["advanced excel", "ms excel", "microsoft excel"]),
    "Spark": ("data", ["pyspark", "apache spark"]),
    "Hadoop": ("data", ["hive", "mapreduce"]),
    "Airflow": ("data", ["apache airflow"]),
    "dbt": ("data", []),
    "ETL": ("data", ["elt", "data pipelines", "data pipeline"]),
    "AWS": ("cloud", ["amazon web services", "ec2", "s3", "lambda"]),
    "Azure": ("cloud", ["microsoft azure"]),
    "Google Cloud": ("cloud", ["gcp", "google cloud platform"]),
    "Docker": ("cloud", ["containerisation", "containerization"]),
    "Kubernetes": ("cloud", ["k8s", "eks", "aks"]),
    "Terraform": ("cloud", ["infrastructure as code"]),
    "Jenkins": ("cloud", []),
    "CI/CD": ("cloud", ["ci cd", "continuous integration", "github actions", "gitlab ci"]),
    "Ansible": ("cloud", []),
    "Linux": ("cloud", ["ubuntu", "unix", "rhel", "centos"]),
    "Nginx": ("cloud", []),
    "Prometheus": ("cloud", ["grafana", "datadog", "observability"]),
    "MySQL": ("db", ["my sql"]),
    "PostgreSQL": ("db", ["postgres", "postgre sql"]),
    "MongoDB": ("db", ["mongo"]),
    "Oracle": ("db", ["oracle db", "oracle database"]),
    "SQL Server": ("db", ["mssql", "ms sql server"]),
    "Redis": ("db", []),
    "Elasticsearch": ("db", ["elastic search", "opensearch"]),
    "Snowflake": ("db", []),
    "BigQuery": ("db", ["big query"]),
    "SQLite": ("db", []),
    "DynamoDB": ("db", []),
    "Selenium": ("test", []),
    "Cypress": ("test", []),
    "Playwright": ("test", []),
    "JUnit": ("test", ["testng"]),
    "PyTest": ("test", ["pytest"]),
    "Jest": ("test", []),
    "Postman": ("test", []),
    "JMeter": ("test", ["load testing", "performance testing"]),
    "Manual testing": ("test", ["functional testing", "regression testing"]),
    "Test automation": ("test", ["automation testing", "automated testing"]),
    "API testing": ("test", []),
    "Git": ("tool", ["github", "gitlab", "bitbucket", "version control"]),
    "Jira": ("tool", ["atlassian"]),
    "Agile": ("tool", ["scrum", "kanban", "sprint planning", "safe agile"]),
    "Confluence": ("tool", []),
    "ServiceNow": ("tool", []),
    "SAP": ("tool", []),
    "Salesforce": ("tool", []),
    "Tally": ("tool", []),
    "MS Office": ("tool", ["microsoft office", "ms word", "powerpoint"]),
    "System design": ("tool", ["architecture design", "solution design"]),
    "Code review": ("tool", []),
    "Technical documentation": ("tool", ["documentation"]),
    "Stakeholder management": ("soft", ["stakeholder communication", "client management"]),
    "Communication": ("soft", ["verbal communication", "written communication", "communication skills"]),
    "Leadership": ("soft", ["team leadership", "people management", "mentoring", "mentorship"]),
    "Problem solving": ("soft", ["problem-solving", "analytical thinking", "analytical skills"]),
    "Requirement gathering": ("soft", ["requirements gathering", "requirement analysis", "business analysis"]),
    "Presentation": ("soft", ["presentation skills"]),
    "Teamwork": ("soft", ["collaboration", "team player", "cross-functional collaboration"]),
    "Time management": ("soft", []),
}


def _compile(needle: str) -> re.Pattern:
    """Boundaries that survive '+' and '#', so 'C' never matches inside 'C++'."""
    left = r"(?<![a-z0-9+#.])" if needle[0].isalnum() else r"(?<![a-z0-9])"
    right = r"(?![a-z0-9+#])" if needle[-1].isalnum() else r"(?![a-z0-9])"
    return re.compile(left + re.escape(needle) + right, re.I)


# longest needle first, so "Spring Boot" wins over "Spring"
ALIASES: List[Tuple[str, str, str, re.Pattern]] = sorted(
    (
        (alias, canon, cat, _compile(alias))
        for canon, (cat, aliases) in TAXONOMY.items()
        for alias in [canon, *aliases]
    ),
    key=lambda row: -len(row[0]),
)
