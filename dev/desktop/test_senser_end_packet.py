"""Length-delimited Senser replies; zero-length USB packets carry no data."""
from collections import deque
from io import BytesIO
import struct
import pytest
from looktool import devicesession
transport,_=devicesession.modules()


def header(size,sequence=1,pfunc=0x40):
    return transport.SonySenserDevice.SenserPacketHeader.pack(size=size,pFunc=pfunc,
        sequence=sequence,version=0,miconType=0,offsetType=0,response=0)


class Wire:
    """Actual read order, not a separate queue chosen by the timeout argument."""
    def __init__(self,replies,*,pid=0x0336):
        self.replies=deque(replies);self.queue=deque();self.reads=[];self.writes=[];self.pid=pid
    def reset(self):pass
    def getId(self):return 0x054c,self.pid
    def write(self,data,ep=0):
        self.writes.append(data)
        self.queue.extend(self.replies.popleft())
    def read(self,size,ep=0,timeout=None):
        self.reads.append((size,timeout))
        if not self.queue:
            try:raise transport.usb.core.USBTimeoutError('no transfer queued',-7,60)
            except transport.usb.core.USBError:raise transport.GenericUsbException()
        part=self.queue.popleft()
        if isinstance(part,Exception):raise part
        if len(part)>size:
            self.queue.appendleft(part[size:]);part=part[:size]
        return part


def reply(payload,sequence=1,*,ends=True,final_end=True):
    parts=[header(len(payload),sequence)]
    for at in range(0,len(payload),32768):
        chunk=payload[at:at+32768];parts.append(chunk)
        if ends and len(chunk)%512==0 and (at+len(chunk)<len(payload) or final_end):parts.append(b'')
    return parts


@pytest.mark.parametrize('last',[512,20992,32768])
@pytest.mark.parametrize('final_end',[False,True])
def test_final_aligned_file_chunk_keeps_v2_no_eager_read_and_next_reply_is_complete(last,final_end):
    first=b'x'*(32768+last);second=b'next result'
    wire=Wire([reply(first,27,final_end=final_end),reply(second,28)])
    channel=transport.SonySenserDevice(wire);channel._sequence=27
    assert channel.sendSenserPacket(0x40,b'first')==(0,first)
    # The recipient's successful v2 trace issued no eager file end reads.
    assert all(timeout is None for _,timeout in wire.reads)
    assert channel.sendSenserPacket(0x40,b'second')==(0,second)
    assert channel._sequence==29 and len(wire.writes)==2


def test_nonempty_next_chunk_is_not_discarded_as_an_end_packet():
    payload=b'A'*32768+b'B'*32768+b'C'*137
    wire=Wire([reply(payload,ends=False)])
    assert transport.SonySenserDevice(wire).sendSenserPacket(0x40,b'request')==(0,payload)


def test_fragmented_payload_and_boundary_empty_packets_preserve_every_byte():
    payload=b'A'*32768+b'B'*132
    wire=Wire([[header(len(payload)),b'A'*12000,b'A'*20768,b'',b'B'*12,b'B'*120]])
    assert transport.SonySenserDevice(wire).sendSenserPacket(0x40,b'request')==(0,payload)


def test_one_megabyte_continuation_keeps_sequence_and_full_output():
    size=transport.SonySenserDevice.SenserMaxSize
    a=b'A'*size;b=b'B'*512
    wire=Wire([[header(size+len(b)),*sum(([a[i:i+32768],b''] for i in range(0,size,32768)),[]),
                header(len(b)),b,b'']])
    channel=transport.SonySenserDevice(wire)
    response,data=channel.sendSenserPacket(0x40,b'request')
    assert response==0 and data==a+b and channel._sequence==2


def test_external_output_stream_is_preserved():
    payload=b'payload'*1024;wire=Wire([reply(payload)]);out=BytesIO()
    assert transport.SonySenserDevice(wire).sendSenserPacket(0x40,b'request',out)==(0,None)
    assert out.getvalue()==payload


def test_reported_body_text_is_rejected_and_no_later_request_is_sent():
    # Captured header fields from the recipient probe; generic text, no body data.
    bad=struct.pack('<IHH',1869375842,27936,26479)+bytes([0,0,0,101])
    wire=Wire([[bad],[header(0,29)]])
    channel=transport.SonySenserDevice(wire);channel._sequence=28
    with pytest.raises(transport.SenserProtocolError,match='expected_sequence=28.*actual_sequence=26479'):
        channel.sendSenserPacket(0x40,b'request')
    with pytest.raises(transport.SenserProtocolError,match='unusable'):
        channel.sendSenserPacket(0x40,b'not replayed')
    assert len(wire.writes)==1 and channel._sequence==28


@pytest.mark.parametrize('raw',[b'12345678',header(4,sequence=2),header(4,pfunc=0x30)])
def test_incomplete_or_mismatched_headers_stop_the_channel(raw):
    wire=Wire([[raw]]);channel=transport.SonySenserDevice(wire)
    with pytest.raises(transport.SenserProtocolError):channel.sendSenserPacket(0x40,b'request')
    assert channel._sequence==1


def test_missing_payload_is_a_failure_not_a_successful_end_timeout():
    wire=Wire([[header(1024),b'x'*512]])
    channel=transport.SonySenserDevice(wire)
    with pytest.raises(transport.GenericUsbException):channel.sendSenserPacket(0x40,b'request')
    with pytest.raises(transport.SenserProtocolError):channel.sendSenserPacket(0x40,b'next')
    assert len(wire.writes)==1 and channel._sequence==1


def test_zero_packet_without_a_following_header_propagates_the_real_timeout():
    wire=Wire([[b'']]);channel=transport.SonySenserDevice(wire)
    with pytest.raises(transport.GenericUsbException):channel.sendSenserPacket(0x40,b'request')
    assert channel._sequence==1


def test_session_failure_marks_unhealthy_and_blocks_further_commands():
    session=transport.Session();session.backend=type('Backend',(),{'reset':lambda self:None})()
    called=[]
    def fail():called.append(1);raise transport.SenserProtocolError('bad packet')
    with pytest.raises(transport.SenserProtocolError):session.call(fail)
    with pytest.raises(RuntimeError,match='알 수 없'):session.call(fail)
    assert called==[1] and not session.healthy


@pytest.mark.parametrize('initial',[False,True])
@pytest.mark.parametrize('written',[0,3])
def test_short_usb_command_writes_fail_without_replay(initial,written):
    class Device:
        default_timeout=0
        _ctx=type('Context',(),{'dispose':lambda self,device:None})()
        def __init__(self):self.calls=[]
        def write(self,ep,data,**kw):self.calls.append((ep,data));return written
    device=Device()
    backend=transport._InitialBackend(device,None) if initial else transport.Bounded(device)
    with pytest.raises(transport.GenericUsbException,match='Short.*USB command write'):
        backend.write(2,b'command')
    assert device.calls==[(2,b'command')]


def test_empty_transfers_still_obey_the_existing_backend_deadline(monkeypatch):
    from array import array
    clock=[0.0]
    monkeypatch.setattr(transport.time,'monotonic',lambda:clock[0])
    class Device:
        _ctx=type('Context',(),{'dispose':lambda self,device:None})()
        default_timeout=0
        def write(self,ep,data):return len(data)
        def read(self,ep,length,timeout):clock[0]+=.5;return array('B')
    backend=transport.Bounded(Device(),budget=1)
    class Driver:
        def reset(self):backend.reset()
        def getId(self):return 0x054c,0x0336
        def read(self,size):return backend.read(0x81,size)
        def write(self,data):return backend.write(2,data)
    channel=transport.SonySenserDevice(Driver())
    with pytest.raises(TimeoutError):channel.sendSenserPacket(0x40,b'request')
    assert clock[0]==1.0 and channel._failed
