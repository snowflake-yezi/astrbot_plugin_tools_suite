import unittest

from features.nickname.domain import (
    BindStatus,
    NicknameBook,
    format_nickname_list,
    parse_nickname_command,
)


class NicknameBookTests(unittest.TestCase):
    def test_from_scope_repairs_invalid_users(self):
        scope = {"users": []}

        book = NicknameBook.from_scope(scope)

        self.assertEqual(book.users, {})
        self.assertIs(scope["users"], book.users)

    def test_match_prefers_longest_nickname_and_returns_all_users(self):
        book = NicknameBook(
            {
                "1": ["老王", "老王头"],
                "2": ["老王头"],
            }
        )

        matched = book.match("at老王头 开会")

        self.assertEqual(matched.nickname, "老王头")
        self.assertEqual(matched.message, "开会")
        self.assertEqual(matched.user_ids, ("1", "2"))

    def test_bind_builds_collection_and_reports_duplicates(self):
        book = NicknameBook({"1": ["队友"]})

        added = book.bind("2", "队友")
        duplicate = book.bind("2", "队友")

        self.assertEqual(added.status, BindStatus.ADDED)
        self.assertEqual(added.collection_size, 2)
        self.assertEqual(duplicate.status, BindStatus.ALREADY_IN_COLLECTION)
        self.assertEqual(book.users["2"], ["队友"])

    def test_parse_command_preserves_optional_slash_behavior(self):
        self.assertEqual(parse_nickname_command("/昵称 新名字"), (True, "新名字"))
        self.assertEqual(parse_nickname_command("昵称"), (True, ""))
        self.assertEqual(parse_nickname_command("其他"), (False, ""))

    def test_format_empty_and_nonempty_lists(self):
        self.assertEqual(format_nickname_list([]), "该用户还没有绑定昵称。")
        self.assertEqual(
            format_nickname_list(["甲", "乙"]),
            "该用户的昵称: 甲, 乙",
        )


if __name__ == "__main__":
    unittest.main()
