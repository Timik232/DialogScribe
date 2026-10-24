"""Tests for live-hints overlap dedup (_dedup_overlap).

Клиент шлёт чанки с перекрытием (~последний кластер прошлого чанка),
сервер вычитает пересечение, чтобы транскрипт не дублировался.
"""

from routers.live_hints import _LiveHintsSession

dedup = _LiveHintsSession._dedup_overlap


class TestDedupOverlap:
    def test_phrase_continues_across_boundary(self):
        # фраза продолжается через границу чанка — общие слова вырезаются
        assert dedup("мы должны обсудить бюджет на следующий", "следующий квартал и расходы") == "квартал и расходы"

    def test_full_duplicate_chunk(self):
        # чанк целиком совпадает с хвостом предыдущего — пустой результат
        assert dedup("привет как дела", "привет как дела") == ""

    def test_single_word_overlap(self):
        assert dedup("давайте начнём с", "с бюджета") == "бюджета"

    def test_no_overlap(self):
        assert dedup("завершаем встречу", "новая тема разговора") == "новая тема разговора"

    def test_punctuation_in_overlap(self):
        # пунктуация не мешает пословному сравнению
        assert dedup("обсудим цену, и", "и сроки поставки") == "сроки поставки"

    def test_case_insensitive(self):
        assert dedup("мы поговорили про", "Про доставку") == "доставку"

    def test_repeated_words_keep_longest_match(self):
        # 'и и' в хвосте: берётся наибольшее пересечение
        assert dedup("повтори и и", "и и снова", ) == "снова"

    def test_tail_shorter_than_overlap_window(self):
        # короткий хвост — тоже работает
        assert dedup("ок", "ок давай") == "давай"

    def test_empty_inputs(self):
        assert dedup("", "текст") == "текст"
        assert dedup("хвост", "") == ""
