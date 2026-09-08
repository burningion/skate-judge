// Judgy Skateboard: PLA prototype enclosure. Units: mm.
// Revision 3, 2026-09-08: physical lid flip and soldered LED lead passages.
// OpenSCAD 2021.01+. See README.md for hardware, tolerances and assembly.
// PCB coordinates follow Adafruit EagleCAD; component shapes are envelopes.

/* [Output] */
part = "assembly"; // [assembly,exploded,interior,base,lid,fit_coupon,led_fit_base,led_fit_lid,layout]
show_electronics = true;
mount_ears = true;
lid_markings = false; // Optional engraving adds first-layer islands and bridges.

/* [Shell] */
case_length = 108;
case_width = 80;
body_height = 20;
wall = 3;
floor_thickness = 3;
lid_thickness = 3;
corner_radius = 7;
fit_clearance = 0.3; // Clearance per side, not total.
mount_hole_diameter = 4.5;

/* [Battery: measure the complete wrapped cell] */
battery_length = 36;
battery_width = 29;
battery_thickness = 5;
battery_xy_clearance = 1.5;
battery_pad = 0.8;

/* [PCB mounting and ports] */
pcb_standoff = 4;
pcb_pilot_diameter = 1.7; // M2 plastic-compatible screws; print coupon first.
usb_opening_width = 16;
usb_opening_bottom = 5;
usb_opening_top = 15;

/* [LED stick] */
stick_length = 51.1;
stick_width = 10.22;
stick_height = 3.2;
stick_clearance = 0.3; // End clearance per side in the top-loading channel.
led_lid_clearance = 0.8;
// Open-top wire passages extend this far outward and inward from each PCB end.
led_wire_end_space = 5;
led_wire_pad_space = 5;
// Y bounds of each passage, measured from the exterior long wall.
led_wire_front = 1;
led_wire_back = 11;
// Keep solder/wires below this height above the enclosure back when closing.
led_wire_top = 11.5;
show_led_wire_envelopes = false;

/* [Hidden] */
$fn = 48;
eps = 0.02;
feather_xy = [8,16];
feather_size = [50.8,22.86];
// The rear 2.2 mm holes are NOT the generic Feather 2.54 mm inset pattern.
feather_holes = [[2.54,2.54],[2.54,20.32],[48.26,1.8415],[48.26,20.955]];
imu_xy = [74,17];
imu_size = [25.4,17.78];
imu_holes = [[2.54,15.24],[22.86,15.24]];
battery_xy = [23,43]; // Inside lower-left of tray, before clearance.
bay_l = battery_length + 2*battery_xy_clearance;
bay_w = battery_width + 2*battery_xy_clearance;
pcb_z = floor_thickness + pcb_standoff;
usb_y = feather_xy[1]+11.43;
nut_af = 5.8; // M3 standard hex nut, clearance included.
nut_h = 2.8;
nut_z = body_height-9; // M3x12 reaches just through the nut at default lid.
boss_r = 6;
bosses = [[7,7],[case_length-7,7],[7,case_width-7],[case_length-7,case_width-7]];
ear_xs = [20,case_length-20];
ear_ys = [-6,case_width+6];
led_pcb_thickness = 1.6;
led_x = (case_length-stick_length)/2;
led_bottom = floor_thickness+2.5;
led_slot_x = led_x-stick_clearance;
led_slot_length = stick_length+2*stick_clearance;
led_slot_front = wall+0.2;
led_slot_back = led_slot_front+led_pcb_thickness+2*fit_clearance;
led_window_x = led_x+0.6;
led_window_length = stick_length-1.2;
led_window_bottom = led_bottom+2.2;
led_window_top = led_bottom+stick_width-0.6;
led_anchor_x = led_slot_x-10;
led_top = led_bottom+stick_width;
led_wire_floor = floor_thickness+1;
led_sample_x = led_x-led_wire_end_space-1;
led_sample_length = stick_length+2*led_wire_end_space+2;
led_sample_depth = led_wire_back+2;

assert(case_length >= 108 && case_width >= 80, "Default PCB layout needs at least 108 x 80 mm.");
assert(wall >= 2.4 && wall <= 3.5 && floor_thickness >= 3, "Keep 2.4-3.5 mm walls and >=3 mm floor; rework layout outside these limits.");
assert(body_height >= 20 && body_height <= 40 && lid_thickness >= 2.4, "Insufficient PCB/port/lid clearance.");
assert(fit_clearance >= 0.15 && fit_clearance <= 0.6, "Use 0.15-0.6 mm clearance per side.");
assert(battery_length > 0 && battery_width > 0 && battery_thickness > 0 && battery_xy_clearance >= 1, "Battery dimensions/clearance invalid.");
assert(battery_xy[0]+bay_l+8 <= case_length-wall, "Battery tray/strap anchor exceeds case length.");
assert(battery_xy[1]+bay_w+2 <= case_width-wall, "Increase case_width for this battery.");
assert(floor_thickness+battery_pad+battery_thickness+3 <= body_height, "Increase body_height: allow 3 mm above cell, no clamping.");
assert(pcb_z+1.6+7+2 <= body_height, "Increase body_height for PCB components.");
assert(usb_opening_top < body_height && usb_opening_bottom >= floor_thickness, "USB opening must fit between floor and lid.");
assert(stick_length >= 50 && stick_length <= 53 && stick_width >= 10 && stick_width <= 12 && stick_height > led_pcb_thickness && stick_height <= 4, "Side channel supports the bare eight-pixel stick; rework layout for other boards.");
assert(stick_clearance >= 0.15 && stick_clearance <= 0.4, "Keep 0.15-0.4 mm LED end clearance so the retaining edges still overlap the PCB.");
assert(led_top+led_lid_clearance+1.2 <= body_height && led_lid_clearance >= 0.5, "Increase body_height for the LED channel and lid stop.");
assert(led_anchor_x-3 >= 13 && led_slot_x+led_slot_length+2 <= case_length-13, "LED channel/strain relief conflicts with corner bosses.");
assert(led_slot_back+2 < feather_xy[1]-2, "LED channel must clear the Feather and its wiring.");
assert(led_wire_end_space >= 3 && led_wire_end_space <= 6 && led_wire_pad_space >= 4 && led_wire_pad_space <= 6, "Use 3-6 mm outside / 4-6 mm inside each LED end; revise guides beyond that.");
assert(led_wire_front >= 0.8 && led_wire_front <= 1.5 && led_wire_back >= 9 && led_wire_back <= 12, "Keep an exterior skin and clearance to the Feather behind the LED passages.");
assert(led_wire_top > led_wire_floor+4 && led_wire_top < led_top-2, "Lead envelope must fit below the lid's corner locators.");
assert(part=="assembly" || part=="exploded" || part=="interior" || part=="base" || part=="lid" || part=="fit_coupon" || part=="led_fit_base" || part=="led_fit_lid" || part=="layout", "Unknown part.");

function enclosure_size() = [case_length,case_width,body_height+lid_thickness];

module rounded_rect(l,w,r) {
    hull() for(x=[r,l-r], y=[r,w-r]) translate([x,y]) circle(r=r);
}
module rounded_box(l,w,h,r=3) {
    linear_extrude(height=h) rounded_rect(l,w,r);
}
module slot(l,w,h) {
    linear_extrude(height=h) hull() for(x=[-(l-w)/2,(l-w)/2]) translate([x,0]) circle(d=w);
}
module nut_cut(h=nut_h) { cylinder(d=nut_af/cos(30),h=h,$fn=6); }

module ears() {
    for(x=ear_xs, y=ear_ys) {
        // Broad, planar roots; these are rigid lugs, not flexures.
        intersection() {
            hull() {
                translate([x,y,0]) cylinder(r=7,h=4.5);
                translate([x-6,y<0?4:case_width-4,0]) cylinder(r=6,h=4.5);
                translate([x+6,y<0?4:case_width-4,0]) cylinder(r=6,h=4.5);
            }
            // End the roots at the inner wall: no step protrudes into the battery bay.
            translate([-1,y<0?-20:case_width-wall,-eps]) cube([case_length+2,wall+20,5]);
        }
    }
}
module tether_lugs() {
    // Independent of ears, retained on the Velcro version.
    for(y=[-3,case_width+3]) translate([case_length/2-9,y-4,0]) rounded_box(18,8,4.5,3);
}
module tie_anchor(x,y) {
    // Strap runs in X, through a short 2.2 mm-high bridge above a sealed floor.
    difference() {
        translate([x-3,y-6,floor_thickness-eps]) rounded_box(6,12,5,1.2);
        translate([x-4,y-4.2,floor_thickness+1.2]) cube([8,8.4,2.2]);
    }
}
module pcb_posts(origin,holes,diameter) {
    for(p=holes) translate([origin[0]+p[0],origin[1]+p[1],floor_thickness-eps])
        cylinder(d=diameter,h=pcb_standoff+eps);
}
module pcb_pilots(origin,holes) {
    for(p=holes) translate([origin[0]+p[0],origin[1]+p[1],floor_thickness+0.4])
        cylinder(d=pcb_pilot_diameter,h=pcb_standoff+eps);
}
module battery_tray() {
    // Rounded edges and a loose, broad fabric strap; no rigid lid pressure.
    difference() {
        translate([battery_xy[0]-2,battery_xy[1]-2,floor_thickness-eps])
            rounded_box(bay_l+4,bay_w+4,5+eps,2);
        translate([battery_xy[0],battery_xy[1],floor_thickness-eps])
            rounded_box(bay_l,bay_w,5+2*eps,1);
        // Strap access through both side walls.
        translate([battery_xy[0]-3,battery_xy[1]+bay_w/2-5,floor_thickness+1])
            cube([bay_l+6,10,6]);
        // Battery lead exit toward the Feather, away from the strap.
        translate([battery_xy[0]+3,battery_xy[1]-3,floor_thickness+1]) cube([10,4,6]);
    }
    tie_anchor(battery_xy[0]-5,battery_xy[1]+bay_w/2);
    tie_anchor(battery_xy[0]+bay_l+5,battery_xy[1]+bay_w/2);
}
module led_channel() {
    // Vertical PCB drops in from the open rim. Lid stops upward travel.
    // Its component face points toward -Y, through the front wall opening.
    translate([led_slot_x-2,wall-eps,floor_thickness-eps])
        cube([led_slot_length+4,led_slot_back+2-wall+eps,led_bottom-floor_thickness+eps]);
    for(x=[led_slot_x-2,led_slot_x+led_slot_length])
        translate([x,wall-eps,floor_thickness-eps])
            cube([2,led_slot_back+2-wall+eps,body_height-floor_thickness+eps]);
    // Rear supports are inboard of both solder zones and grow from the floor.
    // The lid supplies the upper-corner X stops after the wired PCB is inserted.
    for(x=[led_x+led_wire_pad_space+2,led_x+stick_length-led_wire_pad_space-5])
        translate([x,led_slot_back,floor_thickness-eps])
            cube([3,2,led_top+0.5-floor_thickness+eps]);
}
module led_wire_passages() {
    // Actual through-slots across the end walls, not recesses behind a bare PCB.
    // Open to the rim so the solder joints can travel down with the LED board.
    for(x=[led_x-led_wire_end_space,led_x+stick_length-led_wire_pad_space])
        translate([x,led_wire_front,led_wire_floor])
            cube([led_wire_end_space+led_wire_pad_space,led_wire_back-led_wire_front,body_height+1]);
}
module base() {
    difference() {
        union() {
            difference() {
                rounded_box(case_length,case_width,body_height,corner_radius);
                translate([wall,wall,floor_thickness])
                    rounded_box(case_length-2*wall,case_width-2*wall,body_height,corner_radius-wall);
            }
            for(p=bosses) translate([p[0],p[1],0]) cylinder(r=boss_r,h=body_height);
            if(mount_ears) ears();
            tether_lugs();
            pcb_posts(feather_xy,feather_holes,4.2);
            pcb_posts(imu_xy,imu_holes,5.6);
            battery_tray();
            led_channel();
            // Internal strain relief beside the LED's DIN end, clear of the Feather.
            tie_anchor(led_anchor_x,8);
        }
        for(p=bosses) {
            translate([p[0],p[1],nut_z-1]) cylinder(d=3.4,h=body_height);
            translate([p[0],p[1],nut_z]) nut_cut();
            // Load nuts horizontally from the open cavity; roofs bridge 5.8 mm.
            translate([p[0]<(case_length/2)?p[0]:p[0]-9,p[1]-nut_af/2,nut_z]) cube([9,nut_af,nut_h]);
        }
        if(mount_ears) for(x=ear_xs, y=ear_ys)
            translate([x,y,-eps]) cylinder(d=mount_hole_diameter,h=5);
        for(y=[-3,case_width+3]) translate([case_length/2,y,-eps]) slot(10,2.8,5);
        pcb_pilots(feather_xy,feather_holes);
        pcb_pilots(imu_xy,imu_holes);
        led_wire_passages();
        // Open-to-rim ports print without a roof; lid tongues close their upper part.
        translate([-eps,usb_y-usb_opening_width/2,usb_opening_bottom]) cube([wall+2*eps,usb_opening_width,body_height]);
        translate([led_window_x,-eps,led_window_bottom])
            cube([led_window_length,led_slot_front+eps,body_height]);
    }
}
module lid_features_in_case_xy() {
    // Feature positions use the CASE's XY coordinates. lid() maps them into
    // print coordinates so an actual 180-degree flip puts them on these walls.
    difference() {
        union() {
            rounded_box(case_length,case_width,lid_thickness,corner_radius);
            translate([18,case_width-wall-fit_clearance-1.6,lid_thickness-eps])
                cube([case_length-36,1.6,2.4+eps]);
            // Front locating ribs stop short of the LED channel's full-height ends.
            for(span=[[18,led_slot_x-2-fit_clearance],
                      [led_slot_x+led_slot_length+2+fit_clearance,case_length-18]])
                translate([span[0],wall+fit_clearance,lid_thickness-eps])
                    cube([span[1]-span[0],1.6,2.4+eps]);
            for(x=[wall+fit_clearance,case_length-wall-fit_clearance-1.6])
                translate([x,case_width-25,lid_thickness-eps]) cube([1.6,9,2.4+eps]);
            translate([fit_clearance,usb_y-usb_opening_width/2+fit_clearance,lid_thickness-eps])
                cube([wall-2*fit_clearance,usb_opening_width-2*fit_clearance,body_height-usb_opening_top+eps]);
            // Front header of the LED window prints upright on the flat lid.
            translate([led_window_x+fit_clearance,fit_clearance,lid_thickness-eps])
                cube([led_window_length-2*fit_clearance,wall-2*fit_clearance,body_height-led_window_top+eps]);
            // Stop above the board: retains it without clamping or flexing PLA.
            translate([led_slot_x+fit_clearance,led_slot_front+fit_clearance,lid_thickness-eps])
                cube([led_slot_length-2*fit_clearance,led_pcb_thickness,body_height-led_top-led_lid_clearance+eps]);
            // Locate only the upper, unsoldered PCB corners. The lower end
            // passages stay open for wires; these do not squeeze the PCB faces.
            for(x=[led_slot_x-1.6,led_slot_x+led_slot_length])
                translate([x,led_slot_front+fit_clearance,lid_thickness-eps])
                    cube([1.6,led_pcb_thickness,body_height-led_top+1.5+eps]);
        }
        for(p=bosses) translate([p[0],p[1],-eps]) cylinder(d=3.4,h=lid_thickness+1);
        // Off by default: a plain first layer is easier to print reliably.
        if(lid_markings) {
            translate([case_length/2,case_width/2,0.45]) mirror([0,0,1]) linear_extrude(height=0.5)
                text("JUDGY",size=9,halign="center",valign="center",font="Liberation Sans:style=Bold");
            translate([case_length/2,case_width/2-13,0.45]) mirror([0,0,1]) linear_extrude(height=0.5)
                text("DECK / PLA",size=3.5,halign="center",valign="center");
        }
    }
}
module lid() {
    // PRINT: exterior down. Mirroring Y HERE makes the manufactured lid correct.
    // Assembly must use a rotation, never a reflection of the printed solid.
    translate([0,case_width,0]) mirror([0,1,0]) lid_features_in_case_xy();
}
module led_electronics() {
    translate([led_x,led_slot_front+fit_clearance+led_pcb_thickness,led_bottom]) rotate([90,0,0]) {
        color("black") cube([stick_length,stick_width,led_pcb_thickness]);
        // Adafruit stick: 6.35 mm pitch, row center 6.35 mm from the pad edge.
        for(i=[0:7]) color("ivory") translate([0.675+i*6.35,3.85,led_pcb_thickness])
            cube([5,5,stick_height-led_pcb_thickness]);
    }
}
module pcb_envelope(origin,size,holes) {
    translate([origin[0],origin[1],pcb_z]) difference() {
        color([0.08,0.4,0.38]) rounded_box(size[0],size[1],1.6,2.54);
        for(p=holes) translate([p[0],p[1],-eps]) cylinder(d=2.2,h=2);
    }
}
module electronics() {
    led_electronics();
    pcb_envelope(feather_xy,feather_size,feather_holes);
    pcb_envelope(imu_xy,imu_size,imu_holes);
    color("silver") translate([feather_xy[0]-1.14,usb_y-4.6,pcb_z+1.6]) cube([7,9.2,3.4]);
    color([0.22,0.23,0.25]) translate([feather_xy[0]+30,feather_xy[1]+4,pcb_z+1.6]) cube([18,15,3.2]);
    color("ivory") translate([feather_xy[0]+7,feather_xy[1]+18,pcb_z+1.6]) cube([8,7,6]);
    color("ivory") translate([feather_xy[0]+20,usb_y-3,pcb_z+1.6]) cube([5,6,3]);
    color("ivory") for(x=[imu_xy[0]-1,imu_xy[0]+imu_size[0]-4]) translate([x,imu_xy[1]+5,pcb_z+1.6]) cube([5,6,3]);
    color([0.7,0.72,0.75]) translate([battery_xy[0]+battery_xy_clearance,battery_xy[1]+battery_xy_clearance,floor_thickness+battery_pad])
        rounded_box(battery_length,battery_width,battery_thickness,1);
}
module assembled_lid(lift=0) {
    translate([0,case_width,body_height+lid_thickness+lift]) rotate([180,0,0]) lid();
}
module led_solder_envelopes(travel=0) {
    // Assumed solder + insulated-lead keepouts, 0.5 mm clear of passage walls.
    // These are fit fixtures, not a measured model of the user's solder joints.
    for(x=[led_x-led_wire_end_space,led_x+stick_length-led_wire_pad_space])
        translate([x+0.5,led_wire_front+0.5,led_wire_floor+0.5])
            cube([led_wire_end_space+led_wire_pad_space-1,led_wire_back-led_wire_front-1,led_wire_top-led_wire_floor-0.5+travel]);
}
module wired_led_insertion(travel=22) {
    // Sweep each box vertically, preserving the actual PCB/pixel footprint.
    translate([led_x,led_slot_front+fit_clearance,led_bottom])
        cube([stick_length,led_pcb_thickness,stick_width+travel]);
    for(i=[0:7]) translate([led_x+0.675+i*6.35,led_slot_front+fit_clearance-(stick_height-led_pcb_thickness),led_bottom+3.85])
        cube([5,stick_height-led_pcb_thickness,5+travel]);
    led_solder_envelopes(travel);
}
module led_fit_base() {
    translate([-led_sample_x,0,0]) intersection() {
        base();
        translate([led_sample_x,0,-eps]) cube([led_sample_length,led_sample_depth,body_height+2*eps]);
    }
}
module led_fit_lid() {
    translate([-led_sample_x,-(case_width-led_sample_depth),0]) intersection() {
        lid();
        translate([led_sample_x,case_width-led_sample_depth,-eps]) cube([led_sample_length,led_sample_depth,lid_thickness+body_height]);
    }
}
module fit_coupon() {
    // One connected print: M3 nut/bolt fit and three labelled M2 pilot sizes.
    difference() {
        rounded_box(45,20,5,2);
        translate([9,10,2]) nut_cut(4);
        translate([9,10,-eps]) cylinder(d=3.4,h=6);
        for(i=[0:2]) translate([22+8*i,10,1]) cylinder(d=1.6+0.1*i,h=5);
        for(i=[0:2]) translate([22+8*i,4,4.6]) linear_extrude(height=0.5)
            text(str(1.6+0.1*i),size=2.5,halign="center");
    }
}

echo("Main shell incl lid, excluding screw heads",[case_length,case_width,body_height+lid_thickness]);
echo("Base footprint",[case_length,case_width+(mount_ears?26:14)]);
echo("Battery nominal",[battery_length,battery_width,battery_thickness],"loose bay",[bay_l,bay_w]);
echo("Lid screw: M3 x",lid_thickness+9,"mm; standard M3 hex nuts, not nyloc");
echo("Integrated LED: -Y long side; nominal front recess",led_slot_front+fit_clearance-(stick_height-led_pcb_thickness));

if(part=="base") base();
if(part=="lid") lid();
if(part=="fit_coupon") fit_coupon();
if(part=="led_fit_base") led_fit_base();
if(part=="led_fit_lid") led_fit_lid();
if(part=="layout") {
    base();
    translate([case_length+12,0,0]) lid();
    translate([0,case_width+22,0]) fit_coupon();
}
if(part=="assembly" || part=="exploded" || part=="interior") {
    color([0.17,0.24,0.3]) base();
    if(part!="interior") color([0.87,0.55,0.19]) translate([0,part=="exploded"?42:0,0]) assembled_lid(part=="exploded"?30:0);
    if(show_electronics) electronics();
    if(show_led_wire_envelopes) color([0.9,0.15,0.05,0.65]) led_solder_envelopes();
}
