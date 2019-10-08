"""Module containing logic used by the web app for repository analysis."""

import re
from threading import Thread
from itertools import chain
from hashlib import md5
from fastlog import log
from psycopg2 import Error as PG_Error
from easy_postgres import Connection as pg_conn
from engine.preprocessing.repoinfo import RepoInfo
from engine.preprocessing.module_parser import get_modules_from_dir
from engine.nodes.nodeorigin import NodeOrigin
from engine.algorithms.algorithm_runner import run_single_repo, OXYGEN
from engine.errors.user_input import UserInputError
from engine.results.detected_clone import DetectedClone
from engine.results.detection_result import DetectionResult
from .credentials import db_url
from .pg_error_handler import handle_pg_error

_SELECT_REPO_JOIN_STATUS = """SELECT repos.*, states.name AS "status_name", states.description AS "status_desc" """ + \
    """FROM repos JOIN states ON (repos.status = states.id) """


def _get_md5(s):
    """
    Get an MD5 hash of a string.

    """
    return md5(s.encode('utf-8')).hexdigest()


def _get_pattern_id(conn, node):
    dump = node.type2_pattern()
    dump_md5 = _get_md5(dump)

    pattern_id = conn.one("""INSERT INTO patterns ("dump", "hash", "weight", "class") """ +
                          """VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id;""",
                          dump, dump_md5, node.weight, node.node.__class__.__name__)

    if pattern_id is None:
        pattern_id = conn.one(
            """SELECT id FROM patterns WHERE "hash" = %s;""", dump_md5)

    return pattern_id


def _extract_patterns(conn, commit_id, modules):
    nodes = chain.from_iterable(modules)

    for n in nodes:
        pattern_id = _get_pattern_id(conn, n)

        conn.run("""INSERT INTO pattern_instances """ +
                 """(pattern_id, commit_id, "file", "line", col_offset) """ +
                 """VALUES (%s, %s, %s, %s, %s);""",
                 pattern_id, commit_id,
                 n.origin.file, n.origin.line, n.origin.col_offset)


def analyze_repo(repo_info, repo_id, algorithm=OXYGEN):
    """Analyze the repo using the specified algorithm. Store results in db."""
    log.info(f"Analyzing repository: {repo_info}")

    try:
        conn = pg_conn(db_url)

        if repo_info.clone_or_pull():
            log.success(
                f"Repository has been successfully cloned: {repo_info}")

        else:
            log.warning(f"Unable to clone repository: {repo_info}")

            conn.run("""UPDATE repos SET status = (SELECT id FROM states WHERE name = 'err_clone') WHERE id = %s;""",
                     repo_id)

            return

        modules = get_modules_from_dir(repo_info.dir)

        if not modules:
            log.warning("Repository contains no Python module")
            return

        result = run_single_repo(modules, algorithm)

        # Insert repository analysis into database all at once
        with conn.transaction():
            commit_id = conn.one("""INSERT INTO commits (repo_id, hash) VALUES (%s, %s) RETURNING id;""",
                                 repo_id, repo_info.hash)

            for c in result.clones:
                cluster_id = conn.one("""INSERT INTO clusters (commit_id, "value", weight) VALUES (%s, %s, %s) RETURNING id;""",
                                      commit_id, c.value, c.match_weight)

                for o, s in c.origins.items():
                    conn.run("""INSERT INTO origins (cluster_id, file, line, col_offset, similarity) VALUES (%s, %s, %s, %s, %s);""",
                             cluster_id, o.file, o.line, o.col_offset, s)

            log.success(
                f"Repository has been successfully analyzed: {repo_info}")

            conn.run("""UPDATE repos SET status = (SELECT id FROM states WHERE name = 'done') WHERE id = %s;""",
                     repo_id)

        # Once done with the regular analysis, run pattern extraction
        with conn.transaction():
            _extract_patterns(conn, commit_id, modules)

        log.success(
            f"Pattern extraction from was successful: {repo_info}")

    except PG_Error as ex:
        handle_pg_error(ex, conn, repo_id)

    finally:
        conn.close()


def find_repo_results(conn, repo_id):
    """Find existing detection results for this repository in the database."""
    commit_id = conn.one("""SELECT id FROM commits WHERE repo_id = %s ORDER BY analyzed_at DESC LIMIT 1;""",
                         repo_id)

    if commit_id is None:
        return "No commit has been analyzed yet for this repository"

    clones = []

    for c in conn.iter_dict("""SELECT id, "value", weight FROM clusters WHERE commit_id = %s;""", commit_id):
        origins = {}

        for o in conn.iter_dict("""SELECT file, line, col_offset, similarity FROM origins WHERE cluster_id = %s;""", c.id):
            origins[NodeOrigin(o.file, o.line, o.col_offset)] = o.similarity

        clones.append(DetectedClone(c.value, c.weight, origins=origins))

    return DetectionResult(clones)


def _find_repos_select_query(conn, where, params):
    """Helper function for running `SELECT` SQL queries to find repos."""
    return conn.all_dict(_SELECT_REPO_JOIN_STATUS + f"""WHERE ({where});""", params)


def _find_repos_by_metadata(conn, repo_path):
    """Attempt to find the best match for the specified repository."""
    CONDITIONS = [
        # Exact repo name match.
        ("""repos."name" = %s""", repo_path),
        ("""LOWER(repos."name") = LOWER(%s)""", repo_path),
        # Exact user name match.
        ("""repos."user" = %s""", repo_path),
        ("""LOWER(repos."user") = LOWER(%s)""", repo_path),
        # Exact server name match.
        ("""repos."server" = %s""", repo_path),
        ("""LOWER(repos."server") = LOWER(%s)""", repo_path),
        # Partial repo name match.
        ("""repos."name" LIKE %s""", f"{repo_path}%"),
        ("""repos."name" LIKE %s""", f"%{repo_path}"),
        ("""repos."name" LIKE %s""", f"%{repo_path}%"),
        ("""repos."name" ILIKE %s""", f"{repo_path}%"),
        ("""repos."name" ILIKE %s""", f"%{repo_path}"),
        ("""repos."name" ILIKE %s""", f"%{repo_path}%"),
        # Partial user name match.
        ("""repos."user" LIKE %s""", f"{repo_path}%"),
        ("""repos."user" LIKE %s""", f"%{repo_path}"),
        ("""repos."user" LIKE %s""", f"%{repo_path}%"),
        ("""repos."user" ILIKE %s""", f"{repo_path}%"),
        ("""repos."user" ILIKE %s""", f"%{repo_path}"),
        ("""repos."user" ILIKE %s""", f"%{repo_path}%"),
        # Partial server name (e.g., repos."github" instead of repos."github.com").
        ("""repos."server" ILIKE %s""", f"%{repo_path}%")
    ]

    for c in CONDITIONS:
        repos = _find_repos_select_query(conn, *c)

        if repos:
            return repos

    return None


def _try_insert_repo(conn, repo_info):
    """
    Attempt to insert a new entry into the repository database.

    Returns:
        int -- Repository ID if the `INSERT` was successful, `None` otherwise.

    """
    return conn.one("""INSERT INTO repos ("url", "server", "user", "name", "dir", "status") """ +
                    """VALUES (%s, %s, %s, %s, %s, (SELECT id FROM states WHERE name = 'queue')) """ +
                    """ON CONFLICT DO NOTHING RETURNING id;""",
                    repo_info.url, repo_info.server, repo_info.user, repo_info.name, repo_info.dir)


def _get_repo_dict_from_repoinfo(conn, repo_info):
    """
    Find repository ID and status given its RepoInfo.

    Returns:
        Dictionary -- Available keys:
                      `id` - Repository ID,
                      `url`, `server`, `user`, `name`, `dir`,
                      `status` - Status ID,
                      `status_name` - Name of the repo's status,
                      `status_desc` - Verbose status description.

    """
    return conn.one_dict(_SELECT_REPO_JOIN_STATUS + """WHERE repos.url = %s OR repos.dir = %s OR """ +
                         """(repos.server = %s AND repos.user = %s AND repos.name = %s);""",
                         repo_info.url, repo_info.server, repo_info.user, repo_info.name, repo_info.dir)


def _get_repo_summary(conn, repo_dict):
    """Get repo status message or a list of detected clones from the db."""
    # Theoretically, this should never happend, but it's better to check anyways.
    if repo_dict is None:
        return "Database error"

    elif repo_dict.status_name in {"queue", "err_clone", "err_analysis"}:
        return repo_dict.status_desc

    elif repo_dict.status_name == "done":
        return find_repo_results(conn, repo_dict.id)

    else:
        return "Unexpected repository status"


def get_repo_analysis(repo_path):
    """
    Get analysis of a repository given its path.

    Only one of the possible return values will be returned.
    Use `isinstance` to determine the type of the returned value.

    Returns:
        string -- Message describing the state of the repo's analysis.
        DetectionResult -- Detection result retrieved from the database.
        list[dict] -- List of repositories matching the repository path.

    """
    # Strip leading and trailing whitespace from the repo path.
    repo_path = repo_path.strip()
    repo_id = None

    try:
        repo_info = RepoInfo.parse_repo_info(repo_path)

        conn = pg_conn(db_url)

        if not repo_info:
            if re.fullmatch(r"^[\w\.\-]+$", repo_path):
                repos = _find_repos_by_metadata(conn, repo_path)

                # No repository matches the given repository path.
                if not repos:
                    raise UserInputError(
                        "No matching repository found in the database")

                # Exact one matching repository.
                if len(repos) == 1:
                    return _get_repo_summary(conn, repos[0])

                # Multiple repositories match the repository path.
                else:
                    return repos

            else:
                raise UserInputError("Invalid Git repository path format")

        repo_id = _try_insert_repo(conn, repo_info)

        if repo_id is not None:
            Thread(target=analyze_repo, args=(repo_info, repo_id)).start()
            return "The repository has been added to the queue"

        repo_dict = _get_repo_dict_from_repoinfo(conn, repo_info)

        return _get_repo_summary(conn, repo_dict)

    except PG_Error as ex:
        handle_pg_error(ex, conn, repo_id)
        return "Database error"

    finally:
        conn.close()
