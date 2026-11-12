"""A rebooted node must not retire its own boot from a neighbour's old echo."""
import threading,unittest
from service import Agent
from protocol import Protocol,Records

class SelfRecordTests(unittest.TestCase):
    def agent(self):
        agent=Agent.__new__(Agent);agent.config={'id':'BMI30-S07'}
        agent.protocol=Protocol('17'*32);agent.records=Records(agent.protocol)
        agent.lock=threading.RLock();agent.peers={};agent.secrets={};agent.local={};agent.switching=False
        agent.records.merge([agent.protocol.sign({'id':'BMI30-S07','boot':'usb-boot','seq':1})]);return agent
    def incoming(self,agent):
        peer=agent.protocol.sign({'id':'BMI30-M02','boot':'master-boot','seq':5})
        old=agent.protocol.sign({'id':'BMI30-S07','boot':'old-emmc-boot','seq':500})
        return {'_source_id':'BMI30-M02','request_id':'request','self':peer,'records':[old],
                'record_ages':{'BMI30-M02':0,'BMI30-S07':1},'credentials':{}}
    def assert_current_boot_can_publish(self,agent):
        agent.records.merge([agent.protocol.sign({'id':'BMI30-S07','boot':'usb-boot','seq':2})])
        own=agent.records.records['BMI30-S07']['data']
        self.assertEqual((own['boot'],own['seq']),('usb-boot',2))
        self.assertIn('BMI30-M02',agent.records.records)
    def test_incoming_exchange_cannot_replace_own_live_boot_with_emmc_echo(self):
        agent=self.agent();agent.exchange(self.incoming(agent),'192.168.0.154')
        self.assert_current_boot_can_publish(agent)
    def test_outgoing_exchange_reply_cannot_retire_own_live_boot(self):
        agent=self.agent();incoming=self.incoming(agent)
        def rpc(address,purpose,payload,expected,timeout):
            return {'id':'BMI30-M02','request_id':payload['request_id'],'self':incoming['self'],
                    'records':[incoming['self'],*incoming['records']],'record_ages':incoming['record_ages']},0
        agent.rpc=rpc;agent.contact('192.168.0.154','BMI30-M02')
        self.assert_current_boot_can_publish(agent)

if __name__=='__main__':unittest.main()
