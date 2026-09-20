"""WASAPI loopback selection, simulated.

The real thing cannot run on CI/Linux, so these pin the device-choosing logic
against a fake sounddevice module shaped like a Windows machine.
"""
import pytest

from crib.audio import Audio


class FakeWasapiSettings:
    def __init__(self, loopback=False):
        self.loopback = loopback


class FakeSD:
    """Looks like a Windows box: MME, DirectSound and WASAPI host APIs."""

    WasapiSettings = FakeWasapiSettings

    def __init__(self, with_wasapi=True, out_index=7):
        self.with_wasapi = with_wasapi
        self.out_index = out_index

    def query_hostapis(self):
        apis = [
            {"name": "MME", "default_output_device": 1},
            {"name": "Windows DirectSound", "default_output_device": 1},
        ]
        if self.with_wasapi:
            apis.append(
                {"name": "Windows WASAPI", "default_output_device": self.out_index}
            )
        return apis

    def query_devices(self, index=None):
        return {
            "name": "Speakers (Realtek)",
            "max_output_channels": 2,
            "max_input_channels": 0,
            "default_samplerate": 48000.0,
        }


def test_prefers_wasapi_loopback_over_microphone():
    cands = Audio()._candidates(FakeSD())
    assert "WASAPI loopback" in cands[0][0]
    assert cands[0][1]["extra_settings"].loopback is True
    assert cands[0][1]["device"] == 7
    # The microphone stays as a fallback.
    assert "microphone" in cands[-1][0]


def test_loopback_uses_the_device_own_rate_and_channels():
    """Getting these wrong is what makes WASAPI refuse to open."""
    kwargs = Audio()._candidates(FakeSD())[0][1]
    assert kwargs["samplerate"] == 48000
    assert kwargs["channels"] == 2


def test_falls_back_to_microphone_without_wasapi():
    cands = Audio()._candidates(FakeSD(with_wasapi=False))
    assert len(cands) == 1 and "microphone" in cands[0][0]


def test_explicit_device_overrides_autodetect():
    cands = Audio(device=3)._candidates(FakeSD())
    assert len(cands) == 1 and cands[0][1]["device"] == 3


def test_loopback_can_be_disabled():
    cands = Audio(loopback=False)._candidates(FakeSD())
    assert all("WASAPI" not in label for label, _ in cands)


def test_broken_hostapi_query_does_not_crash():
    class Broken(FakeSD):
        def query_hostapis(self):
            raise OSError("PortAudio not initialised")

    cands = Audio()._candidates(Broken())
    assert "microphone" in cands[-1][0]


def test_samplerate_follows_the_opened_stream():
    """The FFT maps bins to Hz, so a 48k device must not be analysed as 44.1k."""
    a = Audio(samplerate=44100)
    kwargs = a._candidates(FakeSD())[0][1]
    assert kwargs["samplerate"] == 48000, "would misreport every frequency"
