# UCG-Max tech specs (full)
source: https://techspecs.ui.com/unifi/cloud-gateways/ucg-max
access: 2026-10-31


## Overview
  = 141.8 x 127.6 x 30 mm
(5.6 x 5 x 1.2")   <Dimensions>
  - [group] UniFi Application Suite
      = True   <Network>
      = True   <Protect>
      = True   <Access>
      = True   <Talk>
      = True   <Connect>
  = 30+   <Managed UniFi Devices>
  = (15) HD
(8) 2K
(5) 4K   <Managed Cameras>
  = 50   <Managed Access Hubs>
  = 300+   <Simultaneous Users Connected>
  = 4   <Max. WAN Port Count>
  = (1) 2.5 GbE RJ45   <Default WAN Ports>
  - [group] Port Layout
      = 5
(2.5G/1G/100M/10M)   <2.5 GbE RJ45>
  = 2.3 Gbps   <IDS/IPS Throughput>  (note: With UniFi OS 4.1 or later.)
  = Compact desktop   <Form Factor>

## Security
  = True   <Stateful Firewall>
  = True   <Application-Aware Layer 7 Firewall>
  = True   <DPI & Traffic Identification>
  = True   <Zone-Based Firewall Advanced Filtering (Regions, Domains, Apps)>
  = True   <Content Filtering>
  = True   <Intrusion Prevention (IPS/IDS)>
  = True   <Ad Blocking>
  = 55,000+
with CyberSecure   <IDS/IPS Signatures>
  = True   <VLAN/Subnet-based Traffic Segmentation>

## VPN & SD-WAN
  = True   <License-Free SD-WAN>
  - [group] Site-to-Site VPN
      = True   <Site Magic>
      = True   <IPsec>
      = True   <OpenVPN>
  - [group] VPN Server
      = True   <Identity Endpoint One-Click VPN>
      = True   <Teleport Zero-Configuration VPN>
      = True   <WireGuard>
      = True   <OpenVPN>
      = True   <L2TP>
  - [group] VPN Client
      = True   <OpenVPN>
      = True   <WireGuard>

## Networking
  = True   <Multi-WAN Load Balancing>
  - [group] Dynamic Routing
      = True   <OSPF>
      = True   <BGP>
  = True   <Advanced QoS>
  = True   <Multicast DNS (mDNS)>
  = True   <Advanced NAT (SNAT / DNAT / Masquerade / NAT Pooling / 1-to-1 NAT)>
  = True   <Integrated RADIUS Server>
  = True   <RADIUS over TLS (RadSec)>
  = True   <Additional Internet Failover with UniFi LTE Backup>
  = True   <Internet Quality and Outage Reporting>
  = 2,000   <MAC Address Table Size>
  = True   <Policy-based WAN and VPN Routing>
  = True   <Customizable DHCP Server>
  = True   <IPv6 ISP Support>
  = True   <IGMP Proxy>
  = True   <Virtual Network Override>
  = True   <All Traffic Flows>

## Hardware
  = Selectable NVMe SSD up to 2 TB   <NVR Storage>  (note: No Storage option does not include NVMe SSD. Third-party SSD installation requires a tray accessory.
512 GB, 1/2 TB option includes tray accessory and NVMe SSD.)
  = 16.1W   <Max. Power Consumption>
  = USB type C (5V DC/5A)   <Power Method>
  = USB type C (5V DC/5A) (Adapter included)   <Power Supply>
  = 100–240V AC   <Supported Voltage Range>
  = 55 BTU/hr   <Heat Dissipation>
  = Quad-core ARM® Cortex®-A53 at 1.5 GHz   <Processor>
  = 3 GB   <System Memory>
  = Without SSD: 460 g (1 lb) 
With SSD: 519 g (1.1 lb)   <Weight>
  = Polycarbonate   <Enclosure Material>
  = 0.96" status display   <LCM Display>
  = Ethernet
Bluetooth   <Management>
  - [group] LEDs
      = True   <Ethernet>
  = (1) Factory reset   <Button>
  = Air: ± 8kV, contact: ± 4kV   <ESD/EMP Protection>
  = -10 to 40° C (14 to 104° F)   <Ambient Operating Temperature>
  = 5 to 95% noncondensing   <Ambient Operating Humidity>
  = True   <NDAA Compliant>
  = CE, FCC, IC, Anatel: 09004-24-08356, SRRC   <Certifications>

## Software
  - [group] Application Requirements
      = UniFi iOS™ and Android™: Version 10.17.2 and later   <Mobile App>