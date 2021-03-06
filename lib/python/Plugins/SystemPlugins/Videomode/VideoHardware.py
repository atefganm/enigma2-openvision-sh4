#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import print_function
from Components.config import config, ConfigSelection, ConfigSubDict, ConfigYesNo
from Components.SystemInfo import BoxInfo
from Tools.CList import CList
from os import path

has_scart = BoxInfo.getItem("scart")
has_yuv = BoxInfo.getItem("yuv")
has_rca = BoxInfo.getItem("rca")
has_avjack = BoxInfo.getItem("avjack")

# The "VideoHardware" is the interface to /proc/stb/video.
# It generates hotplug events, and gives you the list of
# available and preferred modes, as well as handling the currently
# selected mode. No other strict checking is done.

config.av.edid_override = ConfigYesNo(default=False)


class VideoHardware:
	rates = {} # high-level, use selectable modes.
	modes = {}  # a list of (high-level) modes for a certain port.

	rates["PAL"] = {"50Hz": {50: "pal"}}
	rates["NTSC"] = {"60Hz": {60: "ntsc"}}
	rates["480i"] = {"60Hz": {60: "480i"}}
	rates["576i"] = {"50Hz": {50: "576i"}}
	rates["480p"] = {"60Hz": {60: "480p"}}
	rates["576p"] = {"50Hz": {50: "576p"}}
	rates["720p"] = {"50Hz": {50: "720p50"}, "60Hz": {60: "720p"}}
	rates["1080i"] = {"50Hz": {50: "1080i50"}, "59Hz": {60: "1080i59"}, "60Hz": {60: "1080i"}}
	rates["1080p"] = {"23Hz": {50: "1080p23"}, "24Hz": {60: "1080p24"}, "25Hz": {60: "1080p25"}, "29Hz": {60: "1080p29"}, "30Hz": {60: "1080p30"}, "50Hz": {60: "1080p50"}, "59Hz": {60: "1080p59"}, "60Hz": {60: "1080p"}}

	rates["PC"] = {
		"1024x768": {60: "1024x768", 70: "1024x768_70", 75: "1024x768_75", 90: "1024x768_90", 100: "1024x768_100"},
		"1280x1024": {60: "1280x1024", 70: "1280x1024_70", 75: "1280x1024_75"},
		"1600x1200": {60: "1600x1200_60"}
	}

	if has_scart:
		modes["Scart"] = ["PAL"]
	if has_rca:
		modes["RCA"] = ["576i", "PAL"]
	if has_avjack:
		modes["Jack"] = ["PAL", "NTSC", "Multi"]

	modes["HDMI"] = ["720p", "1080p", "1080i", "576p", "576i", "480p", "480i"]
	widescreen_modes = {"720p", "1080p", "1080i"}

	modes["HDMI-PC"] = ["PC"]

	modes["YPbPr"] = modes["HDMI"]

	if "YPbPr" in modes and not has_yuv:
		del modes["YPbPr"]

	if "Scart" in modes and not has_scart and (has_rca or has_avjack):
		modes["RCA"] = modes["Scart"]
		del modes["Scart"]

	if "Scart" in modes and not has_rca and not has_scart and not has_avjack:
		del modes["Scart"]

	def getOutputAspect(self):
		ret = (16, 9)
		port = config.av.videoport.value
		if port not in config.av.videomode:
			print("[Videomode] VideoHardware current port not available in getOutputAspect!!! force 16:9")
		else:
			mode = config.av.videomode[port].value
			force_widescreen = self.isWidescreenMode(port, mode)
			is_widescreen = force_widescreen or config.av.aspect.value in ("16_9", "16_10")
			is_auto = config.av.aspect.value == "auto"
			if is_widescreen:
				if force_widescreen:
					pass
				else:
					aspect = {"16_9": "16:9", "16_10": "16:10"}[config.av.aspect.value]
					if aspect == "16:10":
						ret = (16, 10)
			elif is_auto:
				try:
					print("[Videomode] Read /proc/stb/vmpeg/0/aspect")
					aspect_str = open("/proc/stb/vmpeg/0/aspect", "r").read()
					if aspect_str == "1": # 4:3
						ret = (4, 3)
				except IOError:
					pass
			else:  # 4:3
				ret = (4, 3)
		return ret

	def __init__(self):
		self.last_modes_preferred = []
		self.on_hotplug = CList()
		self.current_mode = None
		self.current_port = None

		self.readAvailableModes()
		self.readPreferredModes()

		if "HDMI-PC" in self.modes and not self.getModeList("HDMI-PC"):
			print("[Videomode] VideoHardware remove HDMI-PC because of not existing modes")
			del self.modes["HDMI-PC"]
		if "Scart" in self.modes and not self.getModeList("Scart"):
			print("[Videomode] VideoHardware remove Scart because of not existing modes")
			del self.modes["Scart"]
		if "YPbPr" in self.modes and not has_yuv:
			del self.modes["YPbPr"]
		if "Scart" in self.modes and not has_scart and (has_rca or has_avjack):
			modes["RCA"] = modes["Scart"]
			del self.modes["Scart"]
		if "Scart" in self.modes and not has_rca and not has_scart and not has_avjack:
			del self.modes["Scart"]

		self.createConfig()

		# take over old AVSwitch component :)
		from Components.AVSwitch import AVSwitch
		config.av.aspectratio.notifiers = []
		config.av.tvsystem.notifiers = []
		config.av.wss.notifiers = []
		AVSwitch.getOutputAspect = self.getOutputAspect

		config.av.colorformat_hdmi = ConfigSelection(choices={"hdmi_rgb": _("RGB"), "hdmi_yuv": _("YUV"), "hdmi_422": _("422")}, default="hdmi_rgb")
		config.av.colorformat_yuv = ConfigSelection(choices={"yuv": _("YUV")}, default="yuv")
		config.av.hdmi_audio_source = ConfigSelection(choices={"pcm": _("PCM"), "spdif": _("SPDIF")}, default="pcm")
		config.av.threedmode = ConfigSelection(choices={"off": _("Off"), "sbs": _("Side by Side"), "tab": _("Top and Bottom")}, default="off")
		config.av.threedmode.addNotifier(self.set3DMode)
		config.av.colorformat_hdmi.addNotifier(self.setHDMIColor)
		config.av.colorformat_yuv.addNotifier(self.setYUVColor)
		config.av.hdmi_audio_source.addNotifier(self.setHDMIAudioSource)

		config.av.aspect.addNotifier(self.updateAspect)
		config.av.wss.addNotifier(self.updateAspect)
		config.av.policy_169.addNotifier(self.updateAspect)
		config.av.policy_43.addNotifier(self.updateAspect)

	def readAvailableModes(self):
		try:
			print("[Videomode] Read /proc/stb/video/videomode_choices")
			modes = open("/proc/stb/video/videomode_choices").read()[:-1]
		except IOError:
			print("[Videomode] Read /proc/stb/video/videomode_choices failed.")
			self.modes_available = []
			return
		self.modes_available = modes.split(' ')

	def readPreferredModes(self):
		if config.av.edid_override.value == False:
			try:
				print("[Videomode] Read /proc/stb/video/videomode_edid")
				modes = open("/proc/stb/video/videomode_edid").read()[:-1]
				self.modes_preferred = modes.split(' ')
				print("[Videomode] VideoHardware reading edid modes: ", self.modes_preferred)
			except IOError:
				print("[Videomode] Read /proc/stb/video/videomode_edid failed.")
				try:
					print("[Videomode] Read /proc/stb/video/videomode_preferred")
					modes = open("/proc/stb/video/videomode_preferred").read()[:-1]
					self.modes_preferred = modes.split(' ')
				except IOError:
					print("[Videomode] Read /proc/stb/video/videomode_preferred failed.")
					self.modes_preferred = self.modes_available

			if len(self.modes_preferred) <= 1:
				self.modes_preferred = self.modes_available
				print("[Videomode] VideoHardware reading preferred modes is empty, using all video modes")
		else:
			self.modes_preferred = self.modes_available
			print("[Videomode] VideoHardware reading preferred modes override, using all video modes")

		self.last_modes_preferred = self.modes_preferred

	# check if a high-level mode with a given rate is available.
	def isModeAvailable(self, port, mode, rate):
		rate = self.rates[mode][rate]
		for mode in rate.values():
			if port == "HDMI-PC":
				if mode not in self.modes_preferred:
					return False
			else:
				if mode not in self.modes_available:
					return False
		return True

	def isWidescreenMode(self, port, mode):
		return mode in self.widescreen_modes

	def setMode(self, port, mode, rate, force=None):
		print("[Videomode] VideoHardware setMode - port:", port, "mode:", mode, "rate:", rate)
		# we can ignore "port"
		self.current_mode = mode
		self.current_port = port
		modes = self.rates[mode][rate]

		mode_50 = modes.get(50)
		mode_60 = modes.get(60)
		mode_24 = modes.get(24)

		if mode_50 is None or force == 60:
			mode_50 = mode_60
		if mode_60 is None or force == 50:
			mode_60 = mode_50
		if mode_24 is None or force:
			mode_24 = mode_60
			if force == 50:
				mode_24 = mode_50

		try:
			print("[Videomode] Write to /proc/stb/video/videomode_50hz")
			open("/proc/stb/video/videomode_50hz", "w").write(mode_50)
			print("[Videomode] Write to /proc/stb/video/videomode_60hz")
			open("/proc/stb/video/videomode_60hz", "w").write(mode_60)
		except IOError:
			print("[Videomode] Write to /proc/stb/video/videomode_50hz failed.")
			print("[Videomode] Write to /proc/stb/video/videomode_60hz failed.")
			try:
				# fallback if no possibility to setup 50/60 hz mode
				print("[Videomode] Write to /proc/stb/video/videomode")
				open("/proc/stb/video/videomode", "w").write(mode_50)
			except IOError:
				print("[Videomode] Write to /proc/stb/video/videomode failed.")

		if BoxInfo.getItem("Has24hz"):
			try:
				print("[Videomode] Write to /proc/stb/video/videomode_24hz")
				open("/proc/stb/video/videomode_24hz", "w").write(mode_24)
			except IOError:
				print("[Videomode] Write to /proc/stb/video/videomode_24hz failed.")

		#call setResolution() with -1,-1 to read the new scrren dimesions without changing the framebuffer resolution
		from enigma import gMainDC
		gMainDC.getInstance().setResolution(-1, -1)

		self.updateAspect(None)
		self.updateColor(port)

	def saveMode(self, port, mode, rate):
		print("[Videomode] VideoHardware saveMode", port, mode, rate)
		config.av.videoport.value = port
		config.av.videoport.save()
		if port in config.av.videomode:
			config.av.videomode[port].value = mode
			config.av.videomode[port].save()
		if mode in config.av.videorate:
			config.av.videorate[mode].value = rate
			config.av.videorate[mode].save()

	def isPortAvailable(self, port):
		# fixme
		return True

	def isPortUsed(self, port):
		if port == "HDMI":
			self.readPreferredModes()
			return len(self.modes_preferred) != 0
		else:
			return True

	def getPortList(self):
		return [port for port in self.modes if self.isPortAvailable(port)]

	# get a list with all modes, with all rates, for a given port.
	def getModeList(self, port):
		print("[Videomode] VideoHardware getModeList for port", port)
		res = []
		for mode in self.modes[port]:
			# list all rates which are completely valid
			rates = [rate for rate in self.rates[mode] if self.isModeAvailable(port, mode, rate)]

			# if at least one rate is ok, add this mode
			if len(rates):
				res.append((mode, rates))
		return res

	def createConfig(self, *args):
		lst = []

		config.av.videomode = ConfigSubDict()
		config.av.videorate = ConfigSubDict()

		# create list of output ports
		portlist = self.getPortList()
		for port in portlist:
			descr = port
			lst.append((port, descr))

			# create list of available modes
			modes = self.getModeList(port)
			if len(modes):
				config.av.videomode[port] = ConfigSelection(choices=[mode for (mode, rates) in modes])
			for (mode, rates) in modes:
				ratelist = []
				for rate in rates:
					if rate in ("auto"):
						if BoxInfo.getItem("Has24hz"):
							ratelist.append((rate, rate))
					else:
						ratelist.append((rate, rate))
				config.av.videorate[mode] = ConfigSelection(choices=ratelist)
		config.av.videoport = ConfigSelection(choices=lst)

	def setConfiguredMode(self):
		port = config.av.videoport.value
		if port not in config.av.videomode:
			print("[Videomode] VideoHardware current port not available, not setting videomode")
			return

		mode = config.av.videomode[port].value

		if mode not in config.av.videorate:
			print("[Videomode] VideoHardware current mode not available, not setting videomode")
			return

		rate = config.av.videorate[mode].value
		self.setMode(port, mode, rate)

	def updateAspect(self, cfgelement):
		# determine aspect = {any,4:3,16:9,16:10}
		# determine policy = {bestfit,letterbox,panscan,nonlinear}

		# based on;
		#   config.av.videoport.value: current video output device
		#     Scart:
		#   config.av.aspect:
		#     4_3:            use policy_169
		#     16_9,16_10:     use policy_43
		#     auto            always "bestfit"
		#   config.av.policy_169
		#     letterbox       use letterbox
		#     panscan         use panscan
		#     scale           use bestfit
		#   config.av.policy_43
		#     pillarbox       use panscan
		#     panscan         use letterbox  ("panscan" is just a bad term, it's inverse-panscan)
		#     nonlinear       use nonlinear
		#     scale           use bestfit

		port = config.av.videoport.value
		if port not in config.av.videomode:
			print("[Videomode] VideoHardware current port not available, not setting videomode")
			return
		mode = config.av.videomode[port].value

		force_widescreen = self.isWidescreenMode(port, mode)

		is_widescreen = force_widescreen or config.av.aspect.value in ("16_9", "16_10")
		is_auto = config.av.aspect.value == "auto"
		policy2 = "policy" # use main policy

		if is_widescreen:
			if force_widescreen:
				aspect = "16:9"
			else:
				aspect = {"16_9": "16:9", "16_10": "16:10"}[config.av.aspect.value]
			policy_choices = {"pillarbox": "panscan", "panscan": "letterbox", "nonlinear": "nonlinear", "scale": "bestfit", "full": "full", "auto": "auto"}
			policy = policy_choices[config.av.policy_43.value]
			policy2_choices = {"letterbox": "letterbox", "panscan": "panscan", "scale": "bestfit", "full": "full", "auto": "auto"}
			policy2 = policy2_choices[config.av.policy_169.value]
		elif is_auto:
			aspect = "any"
			if "auto" in config.av.policy_43.choices:
				policy = "auto"
			else:
				policy = "bestfit"
		else:
			aspect = "4:3"
			policy = {"letterbox": "letterbox", "panscan": "panscan", "scale": "bestfit", "full": "full", "auto": "auto"}[config.av.policy_169.value]

		if not config.av.wss.value:
			wss = "auto(4:3_off)"
		else:
			wss = "auto"

		print("[Videomode] VideoHardware -> setting aspect, policy, policy2, wss", aspect, policy, policy2, wss)
		try:
			print("[Videomode] Write to /proc/stb/video/aspect")
			open("/proc/stb/video/aspect", "w").write(aspect)
		except IOError:
			print("[Videomode] Write to /proc/stb/video/aspect failed.")
		try:
			print("[Videomode] Write to /proc/stb/video/policy")
			open("/proc/stb/video/policy", "w").write(policy)
		except IOError:
			print("[Videomode] Write to /proc/stb/video/policy failed.")
		try:
			print("[Videomode] Write to /proc/stb/denc/0/wss")
			open("/proc/stb/denc/0/wss", "w").write(wss)
		except IOError:
			print("[Videomode] Write to /proc/stb/denc/0/wss failed.")
		try:
			print("[Videomode] Write to /proc/stb/video/policy2")
			open("/proc/stb/video/policy2", "w").write(policy2)
		except IOError:
			print("[Videomode] Write to /proc/stb/video/policy2 failed.")

	def set3DMode(self, configElement):
		print("[Videomode] Write to /proc/stb/video/3d_mode")
		open("/proc/stb/video/3d_mode", "w").write(configElement.value)

	def setHDMIAudioSource(self, configElement):
		print("[Videomode] Write to /proc/stb/hdmi/audio_source")
		open("/proc/stb/hdmi/audio_source", "w").write(configElement.value)

	def setHDMIColor(self, configElement):
		map = {"hdmi_rgb": 0, "hdmi_yuv": 1, "hdmi_422": 2}
		print("[Videomode] Write to /proc/stb/avs/0/colorformat")
		open("/proc/stb/avs/0/colorformat", "w").write(configElement.value)

	def setYUVColor(self, configElement):
		map = {"yuv": 0}
		print("[Videomode] Write to /proc/stb/avs/0/colorformat")
		open("/proc/stb/avs/0/colorformat", "w").write(configElement.value)

	def updateColor(self, port):
		print("[Videomode] VideoHardware updateColor: ", port)
		if port == "HDMI":
			self.setHDMIColor(config.av.colorformat_hdmi)
		elif port == "YPbPr":
			self.setYUVColor(config.av.colorformat_yuv)
		elif port == "Scart":
			map = {"cvbs": 0, "rgb": 1, "svideo": 2, "yuv": 3}
			from enigma import eAVSwitch
			eAVSwitch.getInstance().setColorFormat(map[config.av.colorformat.value])


video_hw = VideoHardware()
video_hw.setConfiguredMode()
