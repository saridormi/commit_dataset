import re
from string import punctuation
from typing import Dict

import pandas as pd
from joblib import Parallel, delayed

from ..utils import BaseProcessor


class MessageProcessor(BaseProcessor):
    """
    This class is used to delete undesirable patterns from messages and filter messages.

    * Reused regexes for deleting emails, urls and SHA from
    Liu, Zhongxin, et al. "Automatic generation of pull request descriptions."
    2019 34th IEEE/ACM International Conference on Automated Software Engineering (ASE). IEEE, 2019.

    * Reused regexes for filtering bot and trivial messages from
    Liu, Zhongxin, et al. "Neural-machine-translation-based commit message generation: how far are we?."
    Proceedings of the 33rd ACM/IEEE International Conference on Automated Software Engineering. 2018.
    """

    @staticmethod
    def get_special_tokens() -> Dict[str, str]:
        return {
            "email": "[EMAIL]",
            "url": "[URL]",
            "at_pattern": "[NICKNAME]",
            "sha": "[COMMIT_ID]",
            "issue_ref": "[ISSUE_ID]",
        }

    @staticmethod
    def _remove_pattern_generic(x: str, pattern: str, replace_pattern: str):
        if not replace_pattern:
            x = re.sub(r"^[:\-\s]*" + pattern + r"[:,.!?;~]*(\s|$)", replace_pattern, x)
            x = re.sub(r"\s[:\-\s]*" + pattern + r"[:,.!?;~]*(?=\s|$)", replace_pattern, x)
        else:
            assert not re.compile(pattern).groups
            x = re.sub(r"((\s|^)[:\-\s]*)" + pattern + r"([:,.!?;~]*(\s|$))", r"\1" + replace_pattern + r"\3", x)
        return x

    @staticmethod
    def _remove_emails(message: str, replace_pattern: str = "") -> str:
        email_pattern = r"\w[\w.\-+]*?@[\w.\-]+?\.[\w.\-]+?"
        email_pattern = r"[\[\({<]?" + email_pattern + r"[\]\)}>]?"
        return MessageProcessor._remove_pattern_generic(message, email_pattern, replace_pattern)

    @staticmethod
    def _remove_urls(message: str, replace_pattern: str = "") -> str:
        url_pattern = r"https?://[-a-zA-Z0-9@:%._+~#?=/&]+?"
        return MessageProcessor._remove_pattern_generic(message, url_pattern, replace_pattern)

    @staticmethod
    def _remove_at_pattern(message: str, replace_pattern: str = "") -> str:
        at_pattern = r"@\S+?"
        return MessageProcessor._remove_pattern_generic(message, at_pattern, replace_pattern)

    @staticmethod
    def _remove_sha(message: str, replace_pattern: str = "") -> str:
        x = message
        # trying to avoid false positives - the SHA pattern unfortunately matches these kinds of dates
        if re.search(r"\d\d\d\d-\d\d-\d\d", x) or re.search(r"\d\d-\d\d-\d\d\d\d", x):
            return x

        for sha_prefix in ["", "ref:", "I"]:
            x = MessageProcessor._remove_pattern_generic(x, sha_prefix + r"[\dA-Fa-f-]{7,}", replace_pattern)
        return x

    @staticmethod
    def _remove_issue_ref(message: str, replace_pattern: str = "") -> str:
        """
        Deletes issue numbers from the following patterns:
        * #123
        * GH-123
        * gh-123
        * ANYTHING-123 (Jira project id)
        """
        x = message
        for pattern in [r"#\d+", r"GH-\d+", r"gh-\d+", r"[A-Z]\w+-\d+"]:
            pattern = r"[\[\({<]?" + pattern + r"[\]\)}>]?"
            x = MessageProcessor._remove_pattern_generic(x, pattern, replace_pattern)
            if not replace_pattern:
                x = x.lstrip("-–:")
        return x

    @staticmethod
    def _remove_signatures(message: str) -> str:
        """
        This method removes various signatures from messages.

        * Not sure about specific tools/repos, but these kinds of signatures appear quite often:
            * `Signed-off-by: <username/email>`
            * `Acked-by: <username/email>`
            * `Co-authored-by: <username/email>`
            * `Also-by: <username>`
            * `Reviewed-by: <username>`
            * `Former commit id: <id>`
            * `git-svn-id: <url>`
            * `Bug: <number>`
            * `Reviewed-on: <url>`
            * `Auto-Submit: <username>`
            * `Commit-Queue: <username>`
            * `Tracked-On: <url>`
            * `(Merged from <url>)`
            * `(Cherry picked from <commit-id>)`
            * `GitOrigin-RevId: <id>`
        * https://github.com/google/moe
            * `Created by MOE: <some link>`
            * `MOE_MIGRATED_REVID=<some number>`
        * https://github.com/facebook/fbshipit:
            * `Differential Revision: <some number>`
            * `Pulled By: <username>`
            * `fbshipit-source-id: <some sha-like string>`
        * https://github.com/google/copybara:
            * `BUG=<some number>`
            * `FIXES=<some number>`
            * `Change-Id: <some sha-like string>`
            * `PiperOrigin-RevId: <some number>`
            * `BAZEL_VERSION_REV_ID: <some number>`
        * https://github.com/kubernetes/sample-apiserver
            * `Kubernetes-commit: <id>`
        * https://github.com/catboost/catboost
            * `Revision: r<number>`
            * `Sandbox task ID: <id>`
            * `Glycine run ID: <id>`
        * https://github.com/luci/luci-go
            * `R=emails/nicknames`
        """
        x = message
        for pattern in [
            r"(signed(-| |)off(-| |)by|co(-| |)?authored(-| |)by|also(-| |)by|reviewed?(-| |)(by|on)|former(-| |)commit(-| |)id|git-svn-id|auto-submit|commit-queue)",
            r"(Created by MOE|MOE_MIGRATED_REVID)",
            r"(fbshipit-source-id|Differential Revision|Pulled(-| )by)",
            r"(Change-Id|PiperOrigin-RevId|BAZEL_VERSION_REV_ID)",
            r"(Kubernetes-commit)",
            r"(Revision: r[\d]*|Sandbox task ID|Glycine run ID)",
            r"(\(Merged from|\(cherry(-| |)picked from|tracked(-| |)on)",
        ]:
            x = re.sub(
                r"(^|\s)" + pattern + r".*?$",
                "",
                x,
                flags=re.IGNORECASE,
            )
        for pattern in [r"(Bug:|BUG=|FIXES=|R=)"]:
            x = re.sub(
                r"^" + pattern + r".*?$",
                "",
                x,
            )
        return x

    @staticmethod
    def _filter_trivial_or_bot(message: str) -> bool:
        message = message.strip()
        # pad punctuation with spaces - expected format in given regular expressions
        message = message.translate(str.maketrans({key: " {0} ".format(key) for key in punctuation}))  # type: ignore
        message = re.sub(" +", " ", message).strip()

        patterns = [
            # for bot messages
            r"^ignore update \' .* \.$",
            # for trivial messages
            r"^update(d)? (changelog|gitignore|readme( . md| file)?)( \.)?$",
            r"^prepare version (v)?[ \d.]+$",
            r"^bump (up )?version( number| code)?( to (v)?[ \d.]+( - snapshot)?)?( \.)?$",
            r"^modify (dockerfile|makefile)( \.)?$",
            r"^update submodule(s)?( \.)?$",
        ]

        for pattern in patterns:
            if re.match(pattern, message, flags=re.IGNORECASE):
                return True

        if re.match(r"^updated? \w+( \. \w+)?$", message, flags=re.IGNORECASE):
            return True

        return False

    @staticmethod
    def _filter_merge(message: str) -> bool:
        return message.startswith("Merge")

    @staticmethod
    def _filter_revert(message: str) -> bool:
        return message.startswith("Revert")

    @staticmethod
    def _filter_squash(message: str, line_sep: str) -> bool:
        message_lines = message.split(line_sep)
        if len(message_lines) == 1:
            return False

        if all(line.strip().startswith("*") for line in message_lines):
            return True

        if all(line.strip().startswith("*") for line in message_lines[1:]):
            return True

        return False

    @staticmethod
    def _filter(message: str, line_sep: str) -> bool:
        # filter strange errors
        if not isinstance(message, str):
            return True

        # filter non-ASCII messages
        if not message.isascii():
            return True

        # filter trivial messages with 1 word
        if len(message.split()) == 1:
            return True

        # filter trivial/bot messages (patterns from NNGen)
        if MessageProcessor._filter_trivial_or_bot(message):
            return True

        # filter merge commits
        if MessageProcessor._filter_merge(message):
            return True

        # filter revert commits
        if MessageProcessor._filter_revert(message):
            return True

        # filter squash commits
        if MessageProcessor._filter_squash(message, line_sep):
            return True

        return False

    @staticmethod
    def _remove_all_patterns(line: str, replace_patterns: bool) -> str:
        if not replace_patterns:
            line = MessageProcessor._remove_emails(line)
            line = MessageProcessor._remove_urls(line)
            line = MessageProcessor._remove_issue_ref(line)
            line = MessageProcessor._remove_signatures(line)
            line = MessageProcessor._remove_at_pattern(line)
            line = MessageProcessor._remove_sha(line)
            line = line.strip()
            return line

        special_tokens = MessageProcessor.get_special_tokens()
        line = MessageProcessor._remove_emails(line, special_tokens["email"])
        line = MessageProcessor._remove_urls(line, special_tokens["url"])
        line = MessageProcessor._remove_issue_ref(line, special_tokens["issue_ref"])
        line = MessageProcessor._remove_signatures(line)
        line = MessageProcessor._remove_at_pattern(line, special_tokens["at_pattern"])
        line = MessageProcessor._remove_sha(line, special_tokens["sha"])
        line = line.strip()
        return line

    @staticmethod
    def _process(message: str, replace_patterns: bool, line_sep: str) -> str:
        if MessageProcessor._filter(message, "\n"):
            return ""

        message_lines = message.split("\n")
        for i, line in enumerate(message_lines):
            line = MessageProcessor._remove_all_patterns(line, replace_patterns)
            message_lines[i] = line
        message = line_sep.join([line for line in message_lines if line])

        if MessageProcessor._filter(message, line_sep):
            return ""

        return message

    def process(self, chunk: pd.DataFrame, line_sep: str, replace_patterns: bool, **kwargs) -> pd.DataFrame:  # type: ignore[override]
        with Parallel(self._n_workers) as pool:
            filtered_messages = pool(
                delayed(MessageProcessor._process)(message, line_sep=line_sep, replace_patterns=replace_patterns)
                for _, message in chunk["message"].items()
            )

        chunk["message"] = filtered_messages
        return chunk.loc[chunk.message.str.len() > 0]
