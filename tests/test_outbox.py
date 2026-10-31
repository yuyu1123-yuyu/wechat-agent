from wechat_agent.infra.wechat.outbox import Outbox


def test_pending_reply_survives_restart_and_keeps_chat_order(tmp_path):
    path = tmp_path / 'outbox.db'
    box = Outbox(path)
    first = box.add('owner', 'first', 'a')
    box.add('owner', 'second', 'a')
    box.retry(first)
    other = box.add('other', 'third', 'b')
    restored = Outbox(path)
    assert restored.size == 3
    assert restored.next()['id'] == other
    restored.finish(other)
    assert restored.next() is None
    restored.finish(first)
    assert restored.next()['text'] == 'second'


def test_uncertain_media_is_held_not_replayed(tmp_path):
    box = Outbox(tmp_path / 'outbox.db')
    ident = box.add('owner', '[[sticker:test]]', 'a')
    box.hold(ident)
    assert box.next() is None
    assert box.size == 0


def test_failed_gui_is_discarded_and_reply_retried(tmp_path, monkeypatch):
    from wechat_agent.infra.wechat.gui import SenderQueue
    sender = SenderQueue(retry=1, outbox_path=tmp_path / 'outbox.db')
    old = object()
    sender._gui = old
    seen = []
    def send(*_):
        seen.append(sender._gui)
        return len(seen) == 2
    monkeypatch.setattr(sender, '_send_via_uia', send)
    monkeypatch.setattr('wechat_agent.infra.wechat.gui.time.sleep', lambda _: None)
    sender.enqueue('owner', 'hello', chat_id='a')
    sender._deliver_pending(sender._outbox.next())
    assert seen == [old, None]
    assert sender.size == 0


def test_delivery_failure_preserves_reply(tmp_path, monkeypatch):
    from wechat_agent.infra.wechat.gui import SenderQueue
    sender = SenderQueue(outbox_path=tmp_path / 'outbox.db')
    monkeypatch.setattr(sender, '_send_with_retry', lambda *_: False)
    sender.enqueue('owner', 'hello', chat_id='a')
    sender._deliver_pending(sender._outbox.next())
    assert sender.size == 1
    assert sender._outbox.next() is None
