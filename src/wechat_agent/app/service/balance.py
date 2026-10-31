"""Account balance alerts; fixed official endpoint, no model calls."""
import json
import time
import urllib.request
from decimal import Decimal
from wechat_agent.infra.llm.api import load_api_key
from wechat_agent.logging_setup import log


class BalanceMonitor:
    def __init__(self, settings, sender, targets):
        self.settings, self.sender, self.targets = settings, sender, targets
        self.path = settings.data_dir / 'balance-alert.json'

    def check(self):
        try:
            key = load_api_key(self.settings.llm.api_key_file,
                               self.settings.llm.api_key_field)
            request = urllib.request.Request('https://api.deepseek.com/user/balance',
                headers={'Authorization': 'Bearer ' + key})
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
            amounts = [Decimal(row['total_balance']) for row in payload['balance_infos']
                       if row['currency'] == 'CNY']
            if not amounts:
                raise ValueError('No CNY balance')
            balance = sum(amounts)
            state = json.loads(self.path.read_text('utf-8')) if self.path.exists() else {}
            if balance >= Decimal('10'):
                state = {}
            elif time.time() - state.get('last_alert', 0) >= 86400:
                for target in self.targets:
                    if target['nick'] == self.settings.wechat.master_chat:
                        self.sender.enqueue(target['nick'],
                            f'提醒你一下，DeepSeek API 余额还剩 {balance:.2f} 元，低于你设的10元了。记得有空补充余额。',
                            chat_id=target['id'])
                        state['last_alert'] = time.time()
                        break
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(state), encoding='utf-8')
            temporary.replace(self.path)
            log(f'[balance] CNY {balance:.2f}; threshold 10')
        except Exception as error:
            log(f'[balance] Check failed: {type(error).__name__}', 'warning')
