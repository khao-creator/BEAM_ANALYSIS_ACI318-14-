import math 
import json
import os
from pydantic import BaseModel, Field 
from typing import List 
import tkinter as tk
from tkinter import ttk, messagebox
import io
from contextlib import redirect_stdout



# ==========================================
# MODULE 0: UNIT CONVERSION SYSTEM
# ==========================================
"""
    PURPOSE: Converts various engineering units to internal system base units.
    HOW IT WORKS: Cleans string inputs, checks the unit type, and applies exact math factors.
    WARNING: Always run data through this class before executing calculation formulas.
"""
class Unit:
    @staticmethod
    def to_cm(value: float, unit: str) -> float:
        # Change unit to cm for geometry math
        unit = unit.lower().strip()
        if unit == "m":
            return value * 100.0
        if unit == "mm":
            return value / 10.0
        if unit == "in":
            return value * 2.54 # Standard conversion factor
        if unit == "cm":
            return value
        raise ValueError(f"Unsupported length unit: '{unit}'. Expected 'm', 'mm', 'in', or 'cm'.")

    @staticmethod
    def to_mm(value: float, unit: str) -> float:
        # Change unit to mm for rebar size
        unit = unit.lower().strip()
        if unit == "cm":
            return value * 10.0
        if unit == "m":
            return value * 1000.0
        if unit == "mm":
            return value
        raise ValueError(f"Unsupported diameter unit: '{unit}'. Expected 'm', 'mm', 'in', or 'cm'.")

    @staticmethod
    def to_kg(value: float, unit: str) -> float:
        # Assuming standard gravity g = 9.80665 m/s^2 -> 1 kN = 1000 N / 9.80665 ≈ 101.9716 kg_f
        unit = unit.lower().strip()
        if unit in ["ton", "t"]:
            return value * 1000.0
        if unit == "kn":
            return value * 101.9716
        if unit == "kg":
            return value
        raise ValueError(f"Unsupported force unit: '{unit}'. Expected 'ton', 't', 'kn', or 'kg'.")

    @staticmethod
    
    def to_kg_m(value: float, unit: str) -> float:
        # Change unit to kg-m for moment calculation
        unit = unit.lower().strip()
        if unit in ["ton-m", "t-m"]:
            return value * 1000.0
        if unit == "kn-m":
            return value * 101.9716
        if unit == "kg-cm":
            return value / 100.0
        if unit == "kg-m":
            return value
        raise ValueError(f"Unsupported moment unit: '{unit}'. Expected 'ton-m', 't-m', 'kn-m', or 'kg-m'.")


# ==========================================
# MODULE 1: DATA INGESTION & VALIDATION
# ==========================================
class RebarLayer(BaseModel):
    """
    PURPOSE: Stores data for a single horizontal layer of reinforcement bars.
    HOW IT WORKS: Keeps track of bar size (dia), count (qty), and vertical spacing.
    NOTE: Layer 1 is the outermost layer. Higher layers count from outside to inside.
    """
    # ... means this value is required. Program will stop if empty.
    dia: float = Field(..., description="Bar diameter in millimeters (mm).")
    qty: int = Field(..., description="Total number of bars present in this specific layer.")
    # Default is 0.0 for the first layer.
    clear_dist: float = Field(0.0, description="Clear distance from the layer below it in centimeters (cm).")

class SectionForces(BaseModel):
    """
    PURPOSE: Groups all ultimate structural design forces at one specific point of the beam.
    HOW IT WORKS: Saves Mu and Vu (required). Automatically sets Pu and Tu to 0.0 if not provided.
    """
    Mu: float # Bending moment load
    Vu: float # Shear force load
    Pu: float = 0.0 # Axial force load
    Tu: float = 0.0 # Torsion moment load

class BeamInputData(BaseModel):
    """
    PURPOSE: The complete data model for a full continuous beam span.
    HOW IT WORKS: Combines dimensions, concrete/steel strengths, forces, and rebar layouts 
    across 3 critical sections (left, middle, right) into a single object.
    """
    fc_prime: float          # Concrete strength
    fy: float                # Main rebar strength
    fy_shear: float          # Stirrup rebar strength
    b: float                 # Beam width
    h: float                 # Beam height
    covering: float          # Concrete cover thickness
    stirrup_dia: float       # Stirrup bar size
    stirrup_spacing: float   # Distance between stirrups
    stirrup_legs: int = 2    # Number of stirrup legs (Default is 2)
    Al_provided_mm2: float = 0.0  # Total area of torsion steel (Default is 0.0)
    
    forcesinitial: SectionForces  # Forces at start/left support
    forcesmid: SectionForces      # Forces at middle section
    forcesend: SectionForces      # Forces at end/right support
    
    topinitial_rebars: List[RebarLayer]  # Top steel bars at left support
    botinitial_rebars: List[RebarLayer]  # Bottom steel bars at left support
    topmid_rebars: List[RebarLayer]      # Top steel bars at middle
    botmid_rebars: List[RebarLayer]      # Bottom steel bars at middle
    topend_rebars: List[RebarLayer]      # Top steel bars at right support
    botend_rebars: List[RebarLayer]      # Bottom steel bars at right support

# ==========================================
# MODULE 2: GEOMETRY, EFFECTIVE DEPTH (d), & REBAR SPACING CHECK
# ==========================================
def calculate_rebar_group_properties(layers: List[RebarLayer], b: float, covering: float, stirrup_dia_mm: float):
    # 1.Check empty case: If no steel layers, return 0 to avoid zero division error
    if not layers:
        return {"total_area_cm2": 0.0, "y_bar_cm": 0.0, "warnings": []}
    # 2.Initialize variables 
    total_area = 0.0 # Total steel area (As)
    sum_area_y = 0.0 # Area * distance for finding centroid
    stirrup_cm = stirrup_dia_mm / 10.0 # Convert stirrup size from mm to cm
    current_y_from_edge = 0.0 # Distance from concrete edge to center of current layer
    prev_dia_cm = 0.0 # Remember previous bar size for spacing math
    
    # 3. Calculate net width inside stirrups
    net_width = b - (2 * covering) - (2 * stirrup_cm)
    warnings = [] # List for holding spacing error messages
    
    # Stop here if beam dimensions are impossible
    if net_width <= 0:
        warnings.append(f"[ERROR] Invalid Geometry: Covering ({covering} cm) and stirrup are bigger than beam width (b={b} cm). No space left for rebars.")

    # 4. Loop through each layer
    for index, layer in enumerate(layers):
        # 4.1 Find steel area for this layer (Ai)
        dia_cm = layer.dia / 10.0 # Convert main rebar from mm to cm
        area_per_bar = (math.pi * (dia_cm ** 2)) / 4.0 # Circle area formula (Pi * d^2 / 4)
        layer_area = layer.qty * area_per_bar # Area of 1 bar * number of bars
        
        # 4.2 Check clear spacing based on standard rules
        if layer.qty > 1: # If there is more than one steel bar, then check the spacing.
            # Standard gap rule: Use 2.5 cm or bar size (whichever is bigger)
            req_gap = max(2.5, dia_cm) 
            # Total width needed = (all bars width) + (all gaps width)
            req_width = (layer.qty * dia_cm) + ((layer.qty - 1) * req_gap)
            # Catch error if bars are too tight to pour concrete
            if net_width > 0 and req_width > net_width:
                warnings.append(f"[SPACING ERROR] Layer {index+1} ({layer.qty}-DB{layer.dia:.0f}) needs {req_width:.1f}cm > Net width {net_width:.1f}cm. Cannot pour concrete!")

        # 4.3 Find distance from edge to center of this layer (yi)
        if index == 0:
            # First layer: covering + stirrup thickness + half of main bar diameter
            current_y_from_edge = covering + stirrup_cm + (dia_cm / 2.0)
        else:
            # Higher layers: previous center + previous half bar + clear gap + current half bar
            current_y_from_edge += (prev_dia_cm / 2.0) + layer.clear_dist + (dia_cm / 2.0)
        
        # 4.4 Accumulate values for centroid calculation
        total_area += layer_area # Accumulate space (Sum Ai)
        sum_area_y += (layer_area * current_y_from_edge) # Sum of Ai * yi
        # 4.5 Save current bar size for the next loop 
        prev_dia_cm = dia_cm
    # 5.Calculate ultimate group centroid (y_bar)
    y_bar = sum_area_y / total_area if total_area > 0 else 0.0
    
    # 6.Return final properties of this rebar group
    return {
        "total_area_cm2": round(total_area, 3), # Total steel area
        "y_bar_cm": round(y_bar, 3), # Distance to centroid (d' or y_bar)
        "warnings": warnings # Spacing check warnings
    }

def calculate_all_sections_geometry(data: BeamInputData):
    results = {}
    sections_map = {
        "INITIAL": {"top": data.topinitial_rebars, "bot": data.botinitial_rebars},
        "MID": {"top": data.topmid_rebars, "bot": data.botmid_rebars},
        "END": {"top": data.topend_rebars, "bot": data.botend_rebars}
    }
    
    # [WARNING] Default d' fallback: covering + stirrup + half of a standard 12mm bar (0.6 cm)
    default_d_prime = data.covering + (data.stirrup_dia / 10.0) + 0.6 
    
    for section_name, rebars in sections_map.items():
        top_props = calculate_rebar_group_properties(rebars["top"], data.b, data.covering, data.stirrup_dia)
        bot_props = calculate_rebar_group_properties(rebars["bot"], data.b, data.covering, data.stirrup_dia)
        
        results[section_name] = {
            "As_top": top_props["total_area_cm2"],
            "d_top_tension": round(data.h - top_props["y_bar_cm"], 3) if top_props["total_area_cm2"] > 0 else None, 
            "top_warnings": top_props["warnings"],
            
            "As_bot": bot_props["total_area_cm2"],
            "d_bot_tension": round(data.h - bot_props["y_bar_cm"], 3) if bot_props["total_area_cm2"] > 0 else None, 
            "bot_warnings": bot_props["warnings"],
            
            "d_prime_top": top_props["y_bar_cm"] if top_props["y_bar_cm"] > 0 else default_d_prime, 
            "d_prime_bot": bot_props["y_bar_cm"] if bot_props["y_bar_cm"] > 0 else default_d_prime
        }
    return results

# =========================================================================
# MODULE 3: FLEXURE ENGINE (With Failure Reporting System)
# =========================================================================

def design_flexure(Mu_kgm: float, Pu_kg: float, b: float, d: float, d_prime: float, fc_prime: float, fy: float):
    warnings = []
    Mu = abs(Mu_kgm * 100.0) # Convert kg-m to kg-cm
    phi_flexure = 0.90 # Strength reduction factor for bending
    Mn_req = Mu / phi_flexure # Required nominal bending moment

    Rn = Mn_req / (b * (d**2)) # Nominal resistance factor
    sqrt_val = 1 - (2 * Rn) / (0.85 * fc_prime)

    # Immediately check if the beam cross-section is too small to withstand the load (Fail-Fast).
    if sqrt_val < 0:
        As_min = max((14 / fy) * b * d, (0.8 * math.sqrt(fc_prime) / fy) * b * d)
        return {
            "status": "FAIL",
            "beam_type": "Invalid Section",
            "REQUIRED_As_TENSION_cm2": 0.0,
            "REQUIRED_As_COMPRESSION_cm2": 0.0,
            "As_min_cm2": round(As_min, 2),
            "max_Mn_tm": 0.0, # The cross-section is unusable; therefore, the maximum power is meaningless.
            "req_Mn_tm": round(abs(Mu_kgm) / 1000.0, 2),
            "warnings": ["[CRITICAL] Required moment exceeds maximum capacity. Concrete will crush. Increase b or h."]
        }

    # Calculate concrete intensity factor (Beta1) based on standard rules
    beta1 = 0.85 if fc_prime <= 280 else max(0.65, 0.85 - 0.05 * ((fc_prime - 280) / 70))
    rho_b = (0.85 * beta1 * fc_prime / fy) * (6120 / (6120 + fy)) # Balanced steel ratio
    rho_max = 0.75 * rho_b # Maximum steel ratio allowed for safety
    As1 = rho_max * b * d # Maximum steel area for singly reinforced beam
    a = (As1 * fy) / (0.85 * fc_prime * b) # Depth of concrete stress block
    Mn1 = As1 * fy * (d - a / 2.0) # Maximum nominal moment for single steel 
    As_min = max((14 / fy) * b * d, (0.8 * math.sqrt(fc_prime) / fy) * b * d) # Minimum steel area

    if Mn_req <= Mn1:
        beam_type = "Singly Reinforced"
        rho_req = (0.85 * fc_prime / fy) * (1 - math.sqrt(sqrt_val)) # Required steel ratio
        As_flexure = rho_req * b * d 
        As_prime = 0.0 
    else:
        beam_type = "Doubly Reinforced"
        
        # Engineering suggestion for better and cheaper design
        warnings.append(f"[SUGGESTION] Moment {Mu_kgm:.1f} kg-m exceeds single steel limit. Beam forced to be Doubly Reinforced. Consider increasing beam depth (h) to reduce steel and save cost.")
        
        Mn2 = Mn_req - Mn1 # Remaining moment for compression steel to carry
        As2 = Mn2 / (fy * (d - d_prime)) # Extra tension steel area for Mn2
        As_flexure = As1 + As2 # Total tension steel area
        
        # Check if compression steel yields
        c = a / beta1 # Distance to neutral axis
        f_s_prime = 6120 * ((c - d_prime) / c) # Calculated stress in compression steel
        
        # Catch unexpected physical behavior
        if f_s_prime <= 0:
            warnings.append("[CRITICAL] Neutral axis is too high. Compression steel is in tension. Beam depth is too small.")
        elif f_s_prime < fy:
            warnings.append(f"[WARNING] Compression steel does not yield (fs' = {f_s_prime:.1f} < fy). This section uses steel inefficiently.")
            
        if f_s_prime >= fy:
            f_s_prime = fy # Limit stress to yield strength
            
        # Fallback logic to prevent crash or division by zero
        if f_s_prime > 0:
            As_prime = (As2 * fy) / f_s_prime # Required compression steel area
        else:
            As_prime = As2 # Emergency fallback value
            
    # 1. Handle axial tension force    
    As_axial = 0.0
    if Pu_kg < 0: 
        Tu = abs(Pu_kg) # Force value for calculation
        phi_axial = 0.90  # Strength factor for tension
        As_axial = Tu / (phi_axial * fy) # Extra steel area for axial tension
        warnings.append(f"[INFO] Axial tension of {abs(Pu_kg):.1f} kg found. Added As_axial = {As_axial:.2f} cm2.")
    
    # 2. Calculate total required steel and compare with minimum limit
    As_total_req = As_flexure + As_axial 
    As_min = max((14 / fy) * b * d, (0.8 * math.sqrt(fc_prime) / fy) * b * d)
    
    # Check if code requires adjusting to minimum steel standard
    if As_total_req < As_min:
        warnings.append(f"[INFO] Calculated steel is less than minimum requirement. Forced to use As_min = {As_min:.2f} cm2.")
        
    As_final_req = max(As_total_req, As_min)

    # 3. Return results map
    return {
        "beam_type": beam_type,
        "Mu_design_kgm": round(Mn_req * phi_flexure / 100, 2),
        "As_flexure_cm2": round(As_flexure, 2),
        "As_axial_cm2": round(As_axial, 2),
        "As_min_cm2": round(As_min, 2),
        "REQUIRED_As_TENSION_cm2": round(As_final_req, 2),
        "REQUIRED_As_COMPRESSION_cm2": round(As_prime, 2),
        "warnings": warnings          # List of warning tags for system output
    }

def check_flexural_capacity(As_provided: float, As_prime_provided: float, Mu_kgm: float, Pu_kg: float, b: float, h: float, d: float, d_prime: float, fc_prime: float, fy: float):
    Es = 2.04e6 # Modulus of elasticity of steel
    eps_cu = 0.003 # Maximum usable strain of concrete
    beta1 = 0.85 if fc_prime <= 280 else max(0.65, 0.85 - 0.05 * ((fc_prime - 280) / 70))
    
    # Calculate the yield strain of steel based on the actual grade received from the JSON file.
    eps_ty = fy / Es 
    
    # Organize steel layers into a list for iteration
    bars = [
        {"depth": d, "area": As_provided},
        {"depth": d_prime, "area": As_prime_provided}
    ]
    
    # 1. Internal function to calculate internal force imbalance (residual)
    def calculate_residual(c_target): # Depth of concrete stress block
        a_target = beta1 * c_target
        if a_target > h:
            a_target = h # Clip stress block depth to beam height
        
        Cc_target = 0.85 * fc_prime * a_target * b # Concrete compression force component
        Cs_total = 0.0 # Reset total compression steel force
        T_total = 0.0 # Reset total tension steel force
        
        for bar in bars:
            if bar["area"] <= 0:
                continue
            if bar["depth"] < c_target:
                strain = eps_cu * (c_target - bar["depth"]) / c_target # Compression strain formula
                stress = min(strain * Es, fy) # Limit steel stress to fy
                Cs_total += bar["area"] * (stress - 0.85 * fc_prime) # Deduct displaced concrete area
            else:
                strain = eps_cu * (bar["depth"] - c_target) / c_target # Tension strain formula
                stress = min(strain * Es, fy) # Limit steel stress to fy
                T_total += bar["area"] * stress # Total tension force component
                
        return (Cc_target + Cs_total - T_total) - Pu_kg # Force balance residual

    # 2. Setup search boundaries for the bisection method
    c_left = 0.001 # Lower boundary limit
    c_right = h * 2.0  # Initial upper boundary limit
    
    res_left = calculate_residual(c_left) # Residual at lower limit
    res_right = calculate_residual(c_right) # Residual at initial upper limit
    
    search_round = 0 # Counter for boundary expansion loops
    max_h_limit = h * 100.0  # Maximum search ceiling limit
    
    # Expand upper boundary until a sign change is found
    while res_left * res_right > 0 and c_right < max_h_limit and search_round < 100:
        c_right *= 2.0
        res_right = calculate_residual(c_right)
        search_round += 1

    # Return fail data map if solution cannot be bracketed
    if res_left * res_right > 0:
        return {
            "status": "NON-CONVERGED",
            "phi_Mn_kgm": 0.0,
            "Utilization_Ratio": 999.000,
            "reason": "[WARNING] [Non-Converged] Neutral axis not bracketed within search limit (0.001 to 100h). Check for extreme axial loading conditions.",
            "eps_t": 0.0,
            "phi_factor": 0.65,
            "c_depth_cm": 0.0
        }

    # 3. Find exact neutral axis depth using Bisection Method
    c = (c_left + c_right) / 2.0 # Initialize midpoint variable
    converged = False # Convergence tracking flag
    
    for i in range(100):
        c = (c_left + c_right) / 2.0
        residual = calculate_residual(c)
        
        if abs(residual) < 0.1: # Solution found if imbalance is close to zero
            converged = True
            break
            
        if residual > 0:
            c_right = c # Move upper boundary inward
        else:
            c_left = c # Move lower boundary inward

    # 4. Use final neutral axis to calculate real physics forces
    a = beta1 * c # Final concrete stress block depth
    is_full_comp = False # Full compression tracking flag
    if a >= h:
        a = h
        is_full_comp = True
        
    Cc = 0.85 * fc_prime * a * b # Final concrete compression force
    Mn_steel = 0.0 # Reset nominal moment from steel layers
    
    for bar in bars:
        if bar["area"] <= 0:
            continue
        if bar["depth"] < c:
            strain = eps_cu * (c - bar["depth"]) / c
            stress = min(strain * Es, fy)
            force = bar["area"] * (stress - 0.85 * fc_prime)
            Mn_steel += force * (h / 2.0 - bar["depth"]) # Moment about geometric center
        else:
            strain = eps_cu * (bar["depth"] - c) / c
            stress = min(strain * Es, fy)
            force = bar["area"] * stress
            Mn_steel += force * (bar["depth"] - h / 2.0) # Moment about geometric center

    eps_t = eps_cu * (d - c) / c # Calculate strain at extreme tension steel

    # 5. Determine failure mode and strength reduction factor (phi)
    # Evaluate strength reduction factor (phi) using explicit step-by-step textbook mathematical proof formats
    if eps_t >= 0.005:
        phi_flexure = 0.90
        zone_name = "Tension-Controlled (Ductile)"
        phi_proof = (
            "\n        [FORMULA]    phi = 0.90 (Constant)"
            f"\n        [EVALUATE]   phi = {phi_flexure:.3f}"
        )
    elif eps_ty < eps_t < 0.005:
        # Adjust the calculation range according to ACI 318-14 variable domain standard.
        phi_flexure = 0.65 + 0.25 * ((eps_t - eps_ty) / (0.005 - eps_ty))
        phi_flexure = max(0.65, min(0.90, phi_flexure))
        zone_name = f"Transition Zone\n        {eps_ty:.5f} < et < 0.005"
        phi_proof = (
            f"\n        [FORMULA]    phi = 0.65 + (et - eps_ty) / (0.005 - eps_ty) * 0.25"
            f"\n        [SUBSTITUTE] phi = 0.65 + ({eps_t:.5f} - {eps_ty:.5f}) / (0.005 - {eps_ty:.5f}) * 0.25"
            f"\n        [EVALUATE]   phi = {phi_flexure:.3f}"
        )
    else:
        phi_flexure = 0.65
        zone_name = "Compression-Controlled Flexural Section (Brittle Concrete Compression Failure Mode)"
        phi_proof = (
            "\n        [FORMULA]    phi = 0.65 (Constant)"
            f"\n        [EVALUATE]   phi = {phi_flexure:.3f}"
        )

    failure_mode = f"{zone_name}\n        Net Tensile Strain (et) = {eps_t:.5f}\n        Strength Reduction Factor (phi) = {phi_flexure:.3f}{phi_proof}\n        ACI 318-14 Table 21.2.2"
    
    if is_full_comp:
        failure_mode += " (Whitney Stress Block at Limit)"
        
    # Append warning tag if numerical solver did not converge perfectly
    if not converged:
        failure_mode = "[WARNING] [Non-Converged] " + failure_mode

    # 6. Calculate nominal moment strength about geometric center (h/2)
    M_Cc = Cc * (h/2.0 - a/2.0)  # Bending moment component from concrete
    Mn = M_Cc + Mn_steel        # Total nominal bending strength
    phi_Mn_kgm = (phi_flexure * Mn) / 100.0  # Apply phi factor and convert to kg-m
    
    # 7. Summarize capacity check results and check compliance tracks
    Ag = b * h
    max_beam_compression = 0.10 * fc_prime * Ag # เกณฑ์แรงอัดสูงสุดที่ยอมให้เป็นพฤติกรรมคาน
    
    is_moment_safe = phi_Mn_kgm >= abs(Mu_kgm)
    is_axial_safe = Pu_kg <= max_beam_compression if Pu_kg > 0 else True
    
    is_strength_safe = is_moment_safe and is_axial_safe
    utilization = round(abs(Mu_kgm) / phi_Mn_kgm, 3) if phi_Mn_kgm > 0 else 999.0
    
    As_min_calc = max((14.0 / fy) * b * d, (0.8 * math.sqrt(fc_prime) / fy) * b * d)
    is_min_steel_ok = As_provided >= As_min_calc
    
    # Practical gross reinforcement ratio limit to prevent spatial congestion/voids
    rho_gross = (As_provided + As_prime_provided) / (b * h)
    is_max_steel_ok = rho_gross <= 0.04
    
    # Ductility assessment according to ACI strain limits
    is_ductile_ok = (eps_t >= 0.004) if abs(Mu_kgm) > 0 else True
    
    is_compliance_safe = is_min_steel_ok and is_max_steel_ok and is_ductile_ok
    is_final_safe = is_strength_safe and is_compliance_safe

    if is_final_safe:
        phi_Mn_tm = phi_Mn_kgm / 1000.0
        Mu_tm = abs(Mu_kgm) / 1000.0
        margin_pct = ((phi_Mn_tm - Mu_tm) / phi_Mn_tm) * 100.0 if phi_Mn_tm > 0 else 0.0
        diagnostic_reason = f"{failure_mode} (phi*Mn={phi_Mn_tm:.3f} t-m | Mu={Mu_tm:.3f} t-m | Margin=+{margin_pct:.2f}%)"
    else:
        reasons = []
        
        # 1. Bending Moment Capacity Check 
        if not is_moment_safe:
            phi_Mn_tm = phi_Mn_kgm / 1000.0
            Mu_tm = abs(Mu_kgm) / 1000.0
            deficit_val = Mu_tm - phi_Mn_tm
            deficit_pct = (deficit_val / phi_Mn_tm) * 100.0 if phi_Mn_tm > 0 else 0.0
            reasons.append(f"Mu exceeds design strength (phi*Mn = {phi_Mn_tm:.3f} t-m | Mu = {Mu_tm:.3f} t-m | Deficit = {deficit_val:.3f} t-m ({deficit_pct:.2f}%))")
        
        # Add a clear alert for instances where axial compressive force exceeds the beam's behavior limits.
        if not is_axial_safe:
            reasons.append(f"Axial compression exceeds beam limit (Pu = {Pu_kg/1000:.2f} tons > 0.10*fc'*Ag = {max_beam_compression/1000:.2f} tons). This section acts as a column and is out of scope for this beam module.")
        
        # 2 & 3. Section Control State and Ductility Limit Verification
        if eps_t < eps_ty:
            reasons.append("Section is compression-controlled (Brittle Concrete Compression Failure Mode)")
            reasons.append(f"Tensile strain below yield limit (et = {eps_t:.5f} < {eps_ty:.5f} according to ACI 318-14 Table 21.2.2)")
        elif eps_t < 0.004:
            reasons.append("Section is in transition zone with restricted ductility")
            reasons.append(f"Tensile strain below ductility limit (et = {eps_t:.5f} < 0.004 according to ACI 318-14 Sec 9.3.3.1)")
            
        # 4. Constructability and Code Detailing Violations (Side Effects of Excessive Steel)
        if not is_min_steel_ok:
            reasons.append(f"Provided steel area is less than ACI minimum limit ({As_min_calc:.3f} cm²)")
            
        if not is_max_steel_ok:
            reasons.append(f"Reinforcement congestion prevents constructability (rho_g = {rho_gross*100:.2f}% exceeds practical limit 4.0%)")
            
        diagnostic_reason = "[CRITICAL] Flexural design criteria failure\n        Reason:\n" + "\n".join([f"        {idx+1}. {r}" for idx, r in enumerate(reasons)])

    # Calculate compression steel strain, stress, and yielding state metrics
    if As_prime_provided > 0 and c > 0:
        eps_s_prime = eps_cu * (c - d_prime) / c
        if c <= d_prime:
            eps_s_prime = 0.0
        
        f_s_prime = min(abs(eps_s_prime) * Es, fy)
        is_comp_yielded = "YES" if f_s_prime >= fy else "NO"
        
        if f_s_prime >= fy:
            flex_branch = "Doubly Reinforced (Compression steel yielded)"
        else:
            flex_branch = "Doubly Reinforced (Compression steel elastic)"
    else:
        eps_s_prime = 0.0
        f_s_prime = 0.0
        is_comp_yielded = "NO"
        flex_branch = "Singly Reinforced"

    return {
        "status": "SAFE" if is_final_safe else "UNSAFE",
        "strength_status": "PASS" if is_strength_safe else "FAIL",
        "min_steel_status": "PASS" if is_min_steel_ok else "FAIL",
        "detailing_status": "PASS" if (is_max_steel_ok and is_ductile_ok) else "FAIL",
        "compliance_status": "PASS" if is_compliance_safe else "FAIL",
        "phi_Mn_kgm": round(phi_Mn_kgm, 2),
        "Utilization_Ratio": utilization,
        "reason": diagnostic_reason,
        "eps_t": round(eps_t, 5),
        "phi_factor": round(phi_flexure, 3),
        "c_depth_cm": round(c, 2),
        "As_min_cm2": round(As_min_calc, 3),
        "As_provided_cm2": round(As_provided, 3),
        "eps_s_prime": round(eps_s_prime, 5),
        "f_s_prime": round(f_s_prime, 2),
        "is_compression_yielded": is_comp_yielded,
        "flexural_branch": flex_branch
    }

def calculate_development_length(dia_mm: float, fc_prime: float, fy: float, clear_spacing_cm: float, clear_cover_cm: float, is_top_bar: bool = False):
    """
    Calculates tension development length (Ld) based on ACI 318M-14 Table 25.4.2.2.
    Empirical coefficients are derived strictly from the SI Edition (MPa constants: 2.1, 1.7, 1.4, 1.1)
    and calibrated directly into Metric MKS units (ksc, cm) to match structural textbooks perfectly.
    """
    if dia_mm <= 0:
        return None
        
    dia_cm = dia_mm / 10.0
    
    # Reinforcement location modification factor (psi_t = 1.3 for top bars, 1.0 otherwise)
    alpha = 1.3 if is_top_bar else 1.0  
    
    # STEP 1: Evaluate geometric arrangement criteria (Favorable Detailing vs Other Cases)
    # Favorable requires clear spacing >= 2*db and clear cover >= db
    is_favorable = (clear_spacing_cm >= 2.0 * dia_cm) and (clear_cover_cm >= dia_cm)
    
    # STEP 2: Map and evaluate the exactly 4 mathematical cases from ACI 318M table
    if is_favorable:
        detailing_str = "Favorable Detailing"
        if dia_mm <= 19.0:
            bar_size_str = "Small Bars (<= DB19)"
            denominator = 6.71
        else:
            bar_size_str = "Large Bars (>= DB22)"
            denominator = 5.43
    else:
        detailing_str = "Other/Confined Spacing"
        if dia_mm <= 19.0:
            bar_size_str = "Small Bars (<= DB19)"
            denominator = 4.47
        else:
            bar_size_str = "Large Bars (>= DB22)"
            denominator = 3.51
            
    # Calculate net straight development length in centimeters
    Ld_cm = (fy * alpha * dia_cm) / (denominator * math.sqrt(fc_prime))
    
    # Code mandate: Tension development length must never be less than 30.0 cm
    Ld_final = max(Ld_cm, 30.0)
    
    # Return structured result map identifying execution branch properties
    return {
        "Ld_m": round(Ld_final / 100.0, 2),
        "detailing_case": detailing_str,
        "bar_size_case": bar_size_str
    }

# =========================================================================
# MODULE 4: SHEAR ENGINE (Pure ACI 318 Shear + Pu Adaptive)
# =========================================================================

def check_shear_capacity(Vu_kg: float, Pu_kg: float, b: float, h: float, d: float, fc_prime: float, fy_shear: float, stirrup_dia_mm: float, spacing_cm: float, num_legs: int = 2, phi_shear: float = 0.85, Tu_kgm: float = 0.0, covering: float = 4.0):
    # Core protection against non-physical geometric properties
    if d is None or d <= 0:
        return {
            "module": "shear", "status": "CANNOT_EVALUATE", "phi_Vn_kg": 0.0, "Utilization_Ratio": "N/A", 
            "strength_status": "FAIL", "detailing_status": "FAIL", "reason": "[ERROR] Invalid effective depth (d).",
            "spacing_check": {"status": "FAIL", "max_cm": 0.0, "provided_cm": spacing_cm, "exceeded_cm": 0.0, "exceeded_pct": 0.0},
            "min_area_check": {"status": "FAIL", "provided": 0.0, "required": 0.0},
            "crushing_check": {"status": "FAIL", "type": "Invalid Geometry"}
        }
    if spacing_cm <= 0:
        return {
            "module": "shear", "status": "UNSAFE", "phi_Vn_kg": 0.0, "Utilization_Ratio": "FAIL", 
            "strength_status": "FAIL", "detailing_status": "FAIL", "reason": "[ERROR] Stirrup spacing must be greater than 0 cm.",
            "spacing_check": {"status": "FAIL", "max_cm": 0.0, "provided_cm": spacing_cm, "exceeded_cm": 0.0, "exceeded_pct": 0.0},
            "min_area_check": {"status": "FAIL", "provided": 0.0, "required": 0.0},
            "crushing_check": {"status": "FAIL", "type": "Invalid Spacing"}
        }

    Vu = abs(Vu_kg)
    stirrup_dia_cm = stirrup_dia_mm / 10.0
    Av = num_legs * (math.pi * (stirrup_dia_cm**2) / 4.0)
    Ag = b * h

    # Calculate nominal concrete shear capacity (Vc) based on axial loading condition
    if Pu_kg > 0:
        comp_multiplier = min(1.0 + Pu_kg / (140.0 * Ag), 1.5)
        Vc = 0.53 * comp_multiplier * math.sqrt(fc_prime) * b * d
    elif Pu_kg < 0:
        Vc = max(0.0, 0.53 * (1.0 + Pu_kg / (35.0 * Ag)) * math.sqrt(fc_prime) * b * d)
    else:
        Vc = 0.53 * math.sqrt(fc_prime) * b * d

    # Calculate nominal stirrups capacity (Vs) and limit verification
    Vs_provided = (Av * fy_shear * d) / spacing_cm
    Vs_max = 2.1 * math.sqrt(fc_prime) * b * d
    phi_Vn = phi_shear * (Vc + Vs_provided)
    
    is_strength_safe = phi_Vn >= Vu
    ur_strength = round(Vu / phi_Vn, 3) if phi_Vn > 0 else 999.0

    # Torsion threshold calculation to identify combined shear-torsion active state (ACI 318-14 Section 22.7.7.1)
    Acp = b * h
    Pcp = 2.0 * (b + h)
    phi_torsion = 0.75
    T_threshold = phi_torsion * 0.27 * math.sqrt(fc_prime) * (Acp**2 / Pcp) # kg-cm
    is_torsion_active = abs(Tu_kgm * 100.0) > T_threshold
    
    crushing_fail = False
    crushing_type = "Pure Shear (Vs <= Vs_max)"
    
    if is_torsion_active:
        # Combined shear-torsion web crushing verification (ACI 318-14 Eq. 22.7.7.1a)
        x1 = b - 2.0 * (covering + stirrup_dia_cm / 2.0)
        y1 = h - 2.0 * (covering + stirrup_dia_cm / 2.0)
        if x1 > 0 and y1 > 0:
            Aoh = x1 * y1
            ph = 2.0 * (x1 + y1)
            v_u = Vu / (b * d)
            v_t = (abs(Tu_kgm * 100.0) * ph) / (1.7 * (Aoh**2))
            combined_stress = math.sqrt(v_u**2 + v_t**2)
            max_stress_limit = phi_shear * ((Vc / (b * d)) + 2.1 * math.sqrt(fc_prime))
            if combined_stress > max_stress_limit:
                crushing_fail = True
            crushing_type = "Combined Shear-Torsion Interaction Limit"
        else:
            crushing_fail = True
            crushing_type = "Invalid Geometry"
    else:
        if Vs_provided > Vs_max:
            crushing_fail = True
            crushing_type = "Pure Shear (Vs > Vs_max Limit)"

    # Determine code allowable maximum geometric stirrups spacing
    Vs_req = max(0.0, (Vu / phi_shear) - Vc)
    Vs_threshold = 1.1 * math.sqrt(fc_prime) * b * d
    smax_geom = min(d / 4.0, 30.0) if Vs_req > Vs_threshold else min(d / 2.0, 60.0)

    # Evaluate minimum shear reinforcement ratio area requirements
    av_s_provided = Av / spacing_cm
    av_s_min = max((0.2 * math.sqrt(fc_prime) * b) / fy_shear, (3.5 * b) / fy_shear)
    
    is_spacing_safe = spacing_cm <= smax_geom
    is_min_area_safe = not (Vu > 0.5 * phi_shear * Vc and av_s_provided < av_s_min)
    is_crushing_safe = not crushing_fail
    
    is_detailing_safe = is_spacing_safe and is_min_area_safe and is_crushing_safe
    is_final_safe = is_strength_safe and is_detailing_safe

    # Gather listing of all detailing/strength violations
    detailing_reasons = []
    if crushing_fail:
        detailing_reasons.append(f"Concrete crushing limit exceeded ({crushing_type})")
    if not is_min_area_safe:
        detailing_reasons.append(f"Stirrup area ratio below code minimum (Av/s={av_s_provided:.3f} < Min={av_s_min:.3f})")
    if not is_spacing_safe:
        detailing_reasons.append(f"Stirrup spacing exceeds code maximum limit ({smax_geom:.1f} cm)")
        
    if is_final_safe:
        reason_str = "Shear strength capacity and detailing are sufficient."
    else:
        reasons_list = []
        if not is_strength_safe:
            reasons_list.append("Strength insufficient (phi*Vn < Vu)")
        for dr in detailing_reasons:
            reasons_list.append(dr)
        # Format diagnostics as numbered items for clean layout
        reason_str = "[CRITICAL] Deficiencies found:\n" + "\n".join([f"        {idx+1}. {r}" for idx, r in enumerate(reasons_list)])

    return {
        "module": "shear",
        "status": "SAFE" if is_final_safe else "UNSAFE",
        "strength_status": "PASS" if is_strength_safe else "FAIL",
        "detailing_status": "PASS" if is_detailing_safe else "FAIL",
        "phi_Vn_kg": round(phi_Vn, 2),
        "Utilization_Ratio": ur_strength,
        "spacing_check": {
            "status": "PASS" if is_spacing_safe else "FAIL",
            "max_cm": round(smax_geom, 1),
            "provided_cm": spacing_cm,
            "exceeded_cm": round(spacing_cm - smax_geom, 1) if not is_spacing_safe else 0.0,
            "exceeded_pct": round(((spacing_cm - smax_geom) / smax_geom) * 100.0, 1) if not is_spacing_safe else 0.0
        },
        "min_area_check": {
            "status": "PASS" if is_min_area_safe else "FAIL",
            "provided": av_s_provided,
            "required": av_s_min
        },
        "crushing_check": {
            "status": "PASS" if is_crushing_safe else "FAIL",
            "type": crushing_type
        },
        "trace": {"Vs_kg": Vs_provided, "Vc_kg": Vc},
        "reason": reason_str
    }

def design_shear(Vu_kg: float, Pu_kg: float, b: float, h: float, d: float, fc_prime: float, fy_shear: float, stirrup_dia_mm: float, num_legs: int = 2, phi_shear: float = 0.85):
    # [STEP 1] Prepare design force and rebar properties
    Vu = abs(Vu_kg)
    stirrup_dia_cm = stirrup_dia_mm / 10.0
    Av = num_legs * (math.pi * (stirrup_dia_cm**2) / 4.0)
    Ag = b * h  
    
    # [STEP 2] Calculate concrete shear strength (Vc) with axial load effects
    if Pu_kg > 0:
        comp_multiplier = min(1.0 + Pu_kg / (140.0 * Ag), 1.5)
        Vc = 0.53 * comp_multiplier * math.sqrt(fc_prime) * b * d
    elif Pu_kg < 0:
        Vc = max(0.0, 0.53 * (1.0 + Pu_kg / (35.0 * Ag)) * math.sqrt(fc_prime) * b * d)
    else:
        Vc = 0.53 * math.sqrt(fc_prime) * b * d

    # [STEP 3] Determine required stirrup area-to-spacing ratio (Av/s)
    vs_req = max(0.0, (Vu / phi_shear) - Vc)
    av_s_shear = vs_req / (fy_shear * d)
    
    # [STEP 4] Apply code minimum steel limits
    av_s_min = max((0.2 * math.sqrt(fc_prime) * b) / fy_shear, (3.5 * b) / fy_shear)
    total_av_s_required = max(av_s_shear, av_s_min)
    
    S_req = Av / total_av_s_required if total_av_s_required > 0 else 9999.0

    # [STEP 5] Check maximum geometric spacing limits
    Vs_threshold = 1.1 * math.sqrt(fc_prime) * b * d
    S_max_geom = min(d / 4.0, 30.0) if vs_req > Vs_threshold else min(d / 2.0, 60.0)
    
    # [STEP 6] Calculate practical spacing (Round down to nearest 2.5 cm)
    S_use_raw = min(S_req, S_max_geom)
    S_use_practical = math.floor(S_use_raw / 2.5) * 2.5
    
    # Catch cross-section failure before applying minimum 5 cm floor limit
    is_unsafe = S_use_practical < 5.0
    S_use_practical = max(5.0, S_use_practical)

    # [STEP 7] Return result data map
    return {
        "status": "UNSAFE" if is_unsafe else "SAFE",
        "RECOMMENDED_SPACING_cm": S_use_practical,
        "warnings": ["[CRITICAL] Section too small: Calculated spacing is below practical limit of 5 cm. Increase beam dimensions!"] if is_unsafe else []
    }

# =========================================================================
# MODULE 5: TORSION ENGINE (ACI 318-14 Metric Precise Engine)
# =========================================================================

def check_torsion_capacity(Tu_kgm: float, Vu_kg: float, Vc_kg: float, b: float, h: float, d: float, covering: float, stirrup_dia_mm: float, spacing_cm: float, fc_prime: float, fy: float, fy_shear: float, num_legs: int = 2, Al_provided_mm2: float = 0.0, phi_torsion: float = 0.75, phi_shear: float = 0.85):
    # [STEP 1] Check invalid effective depth
    if d is None:
        return {
            "status": "CANNOT_EVALUATE", 
            "Utilization_Ratio": 999.0, 
            "reason": "[ERROR] Invalid effective depth (d). Cannot evaluate torsion capacity.", 
            "al_required_mm2": 0.0, "at_per_s_cm2_cm": 0.0, "smax_torsion_cm": 0.0, "checks": []
        }
        
    # Convert torsion load from kg-m to kg-cm
    Tu_kgcm = abs(Tu_kgm * 100.0)
    Vu = abs(Vu_kg)
    stirrup_dia_cm = stirrup_dia_mm / 10.0
    warnings = []

    # Check input Vc range stability
    theoretical_max_Vc = 0.53 * 1.5 * math.sqrt(fc_prime) * b * d
    if Vc_kg < 0.0 or Vc_kg > theoretical_max_Vc:
        warnings.append(f"[WARNING] Vc_kg ({Vc_kg/1000:.2f} t) out of expected theoretical maximum range ({theoretical_max_Vc/1000:.2f} t).")

    # [STEP 2] Gross geometry calculation (Acp, Pcp)
    Acp = b * h
    Pcp = 2.0 * (b + h)

    # Calculate stirrup centerline dimensions (x1, y1)
    x1 = b - 2.0 * (covering + stirrup_dia_cm / 2.0)
    y1 = h - 2.0 * (covering + stirrup_dia_cm / 2.0)
    
    if x1 <= 0 or y1 <= 0:
        return {
            "status": "UNSAFE", 
            "Utilization_Ratio": 999.0, 
            "reason": "[ERROR] Geometry too narrow for the specified concrete covering.", 
            "al_required_mm2": 0.0, "at_per_s_cm2_cm": 0.0, "smax_torsion_cm": 0.0, "checks": []
        }
        
    # Calculate enclosed area (Aoh, Ao) and perimeter (ph) of stirrup centerline
    Aoh = x1 * y1
    ph = 2.0 * (x1 + y1)
    Ao = 0.85 * Aoh
    
    checks = []
    
    # [STEP 3] Torsional threshold and cracking limits
    T_threshold = phi_torsion * 0.27 * math.sqrt(fc_prime) * (Acp**2 / Pcp)
    is_torsion_active = Tu_kgcm > T_threshold
    
    checks.append({
        "check": "torsional_threshold",
        "value_kgm": round(T_threshold / 100.0, 3),
        "tu_kgm": round(Tu_kgm, 3),
        "status": "MUST_CONSIDER" if is_torsion_active else "MAY_NEGLECT"
    })
    
    T_cr = phi_torsion * 1.1 * math.sqrt(fc_prime) * (Acp**2 / Pcp)
    checks.append({"check": "cracking_torsion", "value_kgm": round(T_cr / 100.0, 3)})
    
    # [STEP 4] Combined torsion-shear web crushing check (ACI 318-14 Eq. 22.7.7.1a)
    v_u = Vu / (b * d)
    v_t = (Tu_kgcm * ph) / (1.7 * (Aoh**2))
    combined_stress = math.sqrt(v_u**2 + v_t**2)
    max_stress_limit = phi_shear * ((Vc_kg / (b * d)) + 2.1 * math.sqrt(fc_prime))
    crushing_ok = combined_stress <= max_stress_limit

    # Calculate the concrete web crushing utilization ratio
    ur_web = combined_stress / max_stress_limit if max_stress_limit > 0 else 999.0
    
    checks.append({
        "check": "crushing_limit", 
        "combined_stress_ksc": round(combined_stress, 2), 
        "limit_ksc": round(max_stress_limit, 2), 
        "status": "OK" if crushing_ok else "FAIL"
    })
    
    # [STEP 5] Transverse reinforcement check (Torsion stirrup area At/s)
    at_s = Tu_kgcm / (2.0 * phi_torsion * Ao * fy_shear) if is_torsion_active else 0.0
    provided_at_s = (math.pi * (stirrup_dia_cm**2) / 4.0) / spacing_cm if spacing_cm > 0 else 0.0
    stirrup_ok = (not is_torsion_active) or (provided_at_s >= at_s)
    
    # Combined shear and torsion minimum area requirement
    vs_req = max(0.0, (Vu / phi_shear) - Vc_kg)
    av_s_shear = vs_req / (fy_shear * d)
    total_ratio_required = av_s_shear + 2.0 * at_s
    av_s_min_steel = max((0.2 * math.sqrt(fc_prime) * b) / fy_shear, (3.5 * b) / fy_shear)
    
    governing_stirrup_ratio = max(total_ratio_required, av_s_min_steel) if is_torsion_active else av_s_shear
    provided_total_ratio = (num_legs * (math.pi * (stirrup_dia_cm**2) / 4.0)) / spacing_cm if spacing_cm > 0 else 0.0
    combined_min_stirrup_ok = (not is_torsion_active) or (provided_total_ratio >= governing_stirrup_ratio)
    
    checks.append({
        "check": "transverse_reinforcement_at_s",
        "required_at_s": round(at_s, 5),
        "provided_at_s": round(provided_at_s, 5),
        "status": "OK" if (stirrup_ok and combined_min_stirrup_ok) else "FAIL"
    })
    
    # [STEP 6] Max stirrup spacing limits
    smax = min(ph / 8.0, 30.0) if is_torsion_active else 45.0
    spacing_ok = (spacing_cm <= smax) if spacing_cm > 0 else False
    checks.append({"check": "stirrup_spacing_limit", "smax_cm": round(smax, 1), "status": "OK" if spacing_ok else "FAIL"})
    
    # [STEP 7] Longitudinal reinforcement check (Al requirement)
    al_final_mm2 = 0.0
    long_ok = True
    if is_torsion_active:
        at_s_clamped = max(at_s, (1.78 * b) / fy_shear)
        al_req_cm2 = at_s * ph * (fy_shear / fy)
        al_min_cm2 = (1.33 * math.sqrt(fc_prime) * Acp / fy) - (at_s_clamped * ph * (fy_shear / fy))
        al_final_cm2 = max(al_req_cm2, max(0.0, al_min_cm2))
        al_final_mm2 = al_final_cm2 * 100.0
        long_ok = Al_provided_mm2 >= al_final_mm2
        
    checks.append({
            "check": "longitudinal_torsion_steel", 
            "al_required_mm2": round(al_final_mm2, 1), 
            "al_provided_mm2": Al_provided_mm2, 
            "status": "OK" if long_ok else "FAIL"
    })
        # Evaluate global compliance status across all independent limit states
    is_safe = crushing_ok and stirrup_ok and combined_min_stirrup_ok and spacing_ok and long_ok

        # Determine the controlling demand-to-capacity utilization ratio
    if is_torsion_active:
            ur_at_s = at_s / provided_at_s if provided_at_s > 0 else 999.0
            ur_comb = governing_stirrup_ratio / provided_total_ratio if provided_total_ratio > 0 else 999.0
            ur_spacing = spacing_cm / smax if smax > 0 else 999.0
            ur_al = al_final_mm2 / Al_provided_mm2 if Al_provided_mm2 > 0 else (999.0 if al_final_mm2 > 0 else 0.0)
            true_torsion_ur = max(ur_web, ur_at_s, ur_comb, ur_spacing, ur_al)
    else:
        true_torsion_ur = 0.0
    
    # [STEP 8] Construct the structured diagnostic messages for combined torsion
    if not is_torsion_active:
        reason_str = "[INFO] Torsion load is below threshold limit. Torsion design can be neglected."
    else:
        reasons = []
        if not crushing_ok:
            reasons.append(f"Concrete web crushing limit exceeded (Stress UR = {ur_web:.2f})")
        if not combined_min_stirrup_ok:
            reasons.append(f"Total stirrup area ratio is less than minimum code requirement (Req = {governing_stirrup_ratio:.4f}, Provided = {provided_total_ratio:.4f})")
        if not stirrup_ok:
            reasons.append(f"Provided single-leg torsion stirrup area is insufficient (Req = {at_s:.4f}, Provided = {provided_at_s:.4f})")
        if not spacing_ok:
            reasons.append(f"Stirrup spacing exceeds maximum limit (Max = {smax:.1f} cm, Provided = {spacing_cm} cm)")
        if not long_ok:
            reasons.append(f"Provided longitudinal torsion steel area is insufficient (Req = {al_final_mm2:.1f} mm2, Provided = {Al_provided_mm2:.1f} mm2)")
        
        if not reasons:
            reason_str = "[INFO] Section passed all combined shear and torsion strength checks."
        else:
            # Reformat list of deficiencies as structured numbered items
            reason_str = "[CRITICAL] Deficiencies found:\n" + "\n".join([f"        {idx+1}. {r}" for idx, r in enumerate(reasons)])

    return {
        "status": "SAFE" if is_safe else "UNSAFE",
        "Utilization_Ratio": round(true_torsion_ur, 3),
        "reason": reason_str,
        "al_required_mm2": round(al_final_mm2, 1),
        "at_per_s_cm2_cm": round(at_s, 5),
        "smax_torsion_cm": round(smax, 1),
        "checks": checks,
        "warnings": warnings,
        "Tu_tm": abs(Tu_kgm / 1000.0) if Tu_kgm > 0 else 0.0,
        "T_threshold_tm": round(T_threshold / 100000.0, 3),
        "T_cr_tm": round(T_cr / 100000.0, 3),
        "Aoh_cm2": round(Aoh, 1),
        "ph_cm": round(ph, 1),
        "Ao_cm2": round(Ao, 1)
    }

# =========================================================================
# MODULE 6: GITHUB CLI REPORT ENGINE (Pure ACI 318-14 Layout)
# =========================================================================

def execute_engine(data: BeamInputData):
    # 1. Process section geometry metrics
    geom = calculate_all_sections_geometry(data)
    sections = {"INITIAL": data.forcesinitial, "MID": data.forcesmid, "END": data.forcesend}
    
    print("\n" + "="*85)
    print(" BEAMCAL ENGINE v1.0 - TEXTBOOK MATHEMATICAL PROOF (ACI 318-14)")
    print(f" SECTION SIZE: {data.b:.0f} x {data.h:.0f} cm  | Gross Area Ag = {data.b * data.h:.1f} cm2")
    print(f" MATERIAL    : fc' = {data.fc_prime:.0f} ksc | fy = {data.fy:.0f} ksc | fyt = {data.fy_shear:.0f} ksc")
    print("="*85)

    for sec_name, forces in sections.items():
        g = geom[sec_name]
        Mu, Vu, Pu, Tu = forces.Mu, forces.Vu, forces.Pu, forces.Tu
        is_top_tension = Mu < 0
        
        # Classify tension and compression faces based on bending sign
        d_t = g["d_top_tension"] if is_top_tension else g["d_bot_tension"]
        d_c = g["d_prime_bot"] if is_top_tension else g["d_prime_top"]
        As_t = g["As_top"] if is_top_tension else g["As_bot"]
        As_c = g["As_bot"] if is_top_tension else g["As_top"]

        print(f"\nCRITICAL SECTION: {sec_name}")
        print("-"*85)

        # Cleanly bypass evaluation loops if all structural demand metrics are absolute zero
        if abs(Mu) == 0.0 and abs(Vu) == 0.0 and abs(Pu) == 0.0 and abs(Tu) == 0.0:
            print(" [INFO] No design force. Section skipped.")
            print("-" * 85)
            continue

        if As_t <= 0:
            print(" [ERROR] INVALID REINFORCEMENT: No longitudinal reinforcement defined on tension face.")
            print("-" * 85)
            continue

        # Execute core calculation layers
        flex = check_flexural_capacity(As_t, As_c, Mu, Pu, data.b, data.h, d_t, d_c, data.fc_prime, data.fy)
        shear = check_shear_capacity(Vu, Pu, data.b, data.h, d_t, data.fc_prime, data.fy_shear, data.stirrup_dia, data.stirrup_spacing, data.stirrup_legs, Tu_kgm=Tu, covering=data.covering)
        
        # Calculate intermediate baseline Vc for torsion check consistency
        Ag_calc = data.b * data.h
        if Pu > 0:
            Vc_kg = 0.53 * min(1.0 + Pu / (140.0 * Ag_calc), 1.5) * math.sqrt(data.fc_prime) * data.b * d_t
        elif Pu < 0:
            Vc_kg = max(0.0, 0.53 * (1.0 + Pu / (35.0 * Ag_calc)) * math.sqrt(data.fc_prime) * data.b * d_t)
        else:
            Vc_kg = 0.53 * math.sqrt(data.fc_prime) * data.b * d_t

        torsion = check_torsion_capacity(
            Tu_kgm=Tu, Vu_kg=Vu, Vc_kg=Vc_kg,
            b=data.b, h=data.h, d=d_t, covering=data.covering, stirrup_dia_mm=data.stirrup_dia,
            spacing_cm=data.stirrup_spacing, fc_prime=data.fc_prime, fy=data.fy, fy_shear=data.fy_shear,
            num_legs=data.stirrup_legs, Al_provided_mm2=data.Al_provided_mm2
        )
        
        # -----------------------------------------------------------------
        # DEVELOPMENT LENGTH SECTION (DYNAMIC SPACING & COVER EVALUATION)
        # -----------------------------------------------------------------
        # Select correct rebar group based on actual structural tension behavior
        if sec_name == "INITIAL":
            rebars_list = data.topinitial_rebars if is_top_tension else data.botinitial_rebars
        elif sec_name == "MID":
            rebars_list = data.topmid_rebars if is_top_tension else data.botmid_rebars
        else:
            rebars_list = data.topend_rebars if is_top_tension else data.botend_rebars
            
        max_dia = max([r.dia for r in rebars_list], default=0.0)
        clear_cover_actual = data.covering
        
        # Calculate dynamic clear spacing from the outer layer configuration
        net_width = data.b - (2 * data.covering) - (2 * (data.stirrup_dia / 10.0))
        if len(rebars_list) > 0 and rebars_list[0].qty > 1:
            clear_spacing_actual = (net_width - (rebars_list[0].qty * (max_dia / 10.0))) / (rebars_list[0].qty - 1)
        else:
            clear_spacing_actual = 999.0  # Safe large fallback value if only 1 bar exists
            
        # Execute the refined ACI 318M-14 4-Case formula mapping into Metric ksc constants
        Ld = calculate_development_length(
            dia_mm=max_dia, fc_prime=data.fc_prime, fy=data.fy, 
            clear_spacing_cm=clear_spacing_actual, clear_cover_cm=clear_cover_actual, 
            is_top_bar=is_top_tension
        )

        # -----------------------------------------------------------------
        # 8-STEP MATHEMATICAL PROOF CLI PRINTER
        # -----------------------------------------------------------------
        # Step 1: Geometry Details
        spacing_warnings = g.get("top_warnings", []) + g.get("bot_warnings", [])
        if spacing_warnings:
            print("    [SPACING & CONGESTION WARNINGS]")
            for w in spacing_warnings:
                print(f"      * {w}")

        # Step 2: Demand Bending Moment Check
        print("\n 2. FACTORED DESIGN DEMAND & COEFFICIENT (Mu, Rn)")
        print(f"    - Factored Mu = {abs(Mu):,.2f} kg-m ({abs(Mu)/1000:.3f} t-m)")
        print("    [FORMULA]    Rn = Mu / (phi * b * d²)")
        print(f"    [SUBSTITUTE] Rn = {abs(Mu * 100.0):,.1f} / (0.90 * {data.b:.1f} * {d_t:.2f}²)")
        Rn_val = abs(Mu*100.0) / (0.90 * data.b * (d_t**2)) if d_t > 0 else 0.0
        print(f"    [EVALUATE]   Rn = {Rn_val:.3f} kg/cm²")

        # Step 3: Reinforcement Limits
        print("\n 3. REINFORCEMENT RATIO LIMITS (ACI Code Checking)")
        beta1_calc = 0.85 if data.fc_prime <= 280 else max(0.65, 0.85 - 0.05 * ((data.fc_prime - 280) / 70))
        rho_min_calc = max((14.0 / data.fy), (0.8 * math.sqrt(data.fc_prime) / data.fy))
        rho_b_calc = (0.85 * beta1_calc * data.fc_prime / data.fy) * (6120.0 / (6120.0 + data.fy))
        rho_max_calc = 0.75 * rho_b_calc
        print(f"    - Concrete Parameter beta1 = {beta1_calc:.3f}")
        print(f"    - Minimum Steel Ratio rho_min = {rho_min_calc:.5f}")
        print(f"    - Maximum Steel Ratio rho_max = {rho_max_calc:.5f}  (rho_b = {rho_b_calc:.5f})")

        # Step 4 & 5: Provided Areas
        print("\n 4 & 5. THEORETICAL REQUIRED VS PROVIDED REINFORCEMENT AREA")
        print(f"    - Tension Face Steel Provided (As)     = {As_t:.3f} cm2")
        print(f"    - Compression Face Steel Provided (As') = {As_c:.3f} cm2")

        # Step 6: Internal Forces Equilibrium Check [SEPARATED COMPLIANCE DISPLAY PIPELINE]
        print("\n 6. SECTION INTERNAL FORCES EQUILIBRIUM & STRENGTH (phi*Mn)")
        print(f"    - Neutral Axis Depth (c)  = {flex['c_depth_cm']:.2f} cm")
        print(f"    - Stress Block Depth (a)  = {(beta1_calc * flex['c_depth_cm']):.2f} cm")
        print(f"    - Net Tensile Strain (et) = {flex['eps_t']:.5f} -> Factor phi = {flex['phi_factor']:.3f}")
        
        # Print compression steel behavior trace lines for debugging tracking
        print(f"    - Compression Steel Strain (es') = {flex['eps_s_prime']:.5f}")
        print(f"    - Compression Steel Stress (fs') = {flex['f_s_prime']:.2f} kg/cm²")
        print(f"    - Compression Steel Yielded      = {flex['is_compression_yielded']}")
        print(f"    - Flexural Design Branch         = {flex['flexural_branch']}")
        
        print("\n    [STRENGTH CHECK]")
        Mn_calc = (flex['phi_Mn_kgm'] / flex['phi_factor']) if flex['phi_factor'] > 0 else 0.0
        print(f"    - Nominal Moment Capacity (Mn) = {Mn_calc/1000:.3f} t-m")
        print(f"    - Design Strength phi*Mn  = {flex['phi_Mn_kgm']/1000:.3f} t-m")
        print(f"    - Factored Demand Moment (Mu)  = {abs(Mu)/1000:.3f} t-m")
        print("    - [VERIFY]     phi*Mn >= Mu")
        
        # Inject standard governing statement context when strength passes but minimum detailing fails
        ur_flex_val = flex['Utilization_Ratio']
        ur_flex_str = f"{ur_flex_val:.3f}" if isinstance(ur_flex_val, (int, float)) else str(ur_flex_val)
        if flex['strength_status'] == "PASS" and flex['compliance_status'] == "FAIL":
            print(f"    - Strength Result        : {flex['strength_status']} (UR = {ur_flex_str}) (Governing status controlled by code compliance check)")
        else:
            print(f"    - Strength Result        : {flex['strength_status']} (UR = {ur_flex_str})")
            
        # Integrate geometry spacing constraints with structural limit state statuses
        has_spacing_error = len(spacing_warnings) > 0
        derived_detailing = "FAIL" if (flex['detailing_status'] == "FAIL" or has_spacing_error) else "PASS"
        derived_overall = "FAIL" if (flex['status'] == "UNSAFE" or has_spacing_error) else "PASS"
        final_flexure_status = "UNSAFE" if derived_overall == "FAIL" else "SAFE"

        print("\n    [CODE COMPLIANCE CHECK]")
        print(f"    - Required Minimum Steel (As,min) = {flex['As_min_cm2']:.3f} cm2")
        print(f"    - Provided Steel Area (As)        = {flex['As_provided_cm2']:.3f} cm2")
        print(f"    - Minimum Steel Requirement : {flex['min_steel_status']}")
        print(f"    - Detailing Requirement     : {derived_detailing}")
        print(f"    - Strength Requirement      : {flex['strength_status']}")
        print(f"\n    - Overall Flexural Design   : {derived_overall}")
        
        print(f"\n    - FINAL FLEXURE STATUS   : {final_flexure_status}")
        print("      [Diagnostic Reason]    :")
        
        if final_flexure_status == "UNSAFE":
            reasons_list = []
            raw_reason = flex['reason']
            
            # Parse and clean existing prioritized structural diagnostic messages
            if "[CRITICAL] Flexural design criteria failure" in raw_reason:
                lines = raw_reason.split('\n')
                for line in lines:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("[CRITICAL]") and not stripped.startswith("Reason:"):
                        cleaned_line = stripped.lstrip('0123456789. ')
                        if cleaned_line:
                            reasons_list.append(cleaned_line)
            else:
                reasons_list.append(raw_reason.strip())
                
            # Dynamically inject physical constructability failures into the report array
            if has_spacing_error:
                reasons_list.append("Reinforcement clear spacing violates ACI constructability limits (Concrete cannot be poured safely)")
                
            print("        [CRITICAL] Flexural design criteria failure")
            print("        Reason:")
            for idx, r in enumerate(reasons_list):
                print(f"        {idx+1}. {r}")
        else:
            if "\n" in flex['reason']:
                print(f"        {flex['reason']}")
            else:
                print(f"        - {flex['reason']}")

        # Step 7: Shear Verification [FIXED REPORTING RAM PIPELINE]
        print("\n 7. NOMINAL CONCRETE SHEAR CAPACITY VERIFICATION (Vc, Vs)")
        print(f"    - Factored Vu = {Vu/1000:.3f} tons")
        print(f"    - Concrete Shear Cap (phi*Vc) = {(0.85 * Vc_kg)/1000:.3f} tons")
        print(f"    - Stirrup Setup : DB{data.stirrup_dia:.0f} @ {data.stirrup_spacing:.1f} cm (Legs: {data.stirrup_legs})")
        
        t = shear.get('trace', {}) 
        Vs_actual = t.get('Vs_kg', 0.0)
        phi_Vn_actual = shear.get('phi_Vn_kg', 0.0)
        
        print("\n    [STRENGTH CHECK]")
        print("    [FORMULA]    phi*Vn = phi * (Vc + Vs)")
        print(f"    [CAPACITY]   phi*Vn = 0.85 * ({Vc_kg/1000:.3f} + {Vs_actual/1000:.3f}) = {phi_Vn_actual/1000:.3f} tons")
        print(f"    [DEMAND V]   Vu     = {Vu/1000:.3f} tons")
        print("    [VERIFY]     phi*Vn >= Vu")
        
        # Format the utilization ratio value cleanly
        ur_val = shear['Utilization_Ratio']
        ur_str = f"{ur_val:.3f}" if isinstance(ur_val, (int, float)) else str(ur_val)
        
        # Print strength status with refined governance context flags
        if shear['strength_status'] == "PASS" and shear['detailing_status'] == "FAIL":
            print(f"    - Strength Result        : {shear['strength_status']} (UR = {ur_str}) (Governing status controlled by detailing check)")
        else:
            print(f"    - Strength Result        : {shear['strength_status']} (UR = {ur_str})")
        
        print("\n    [DETAILING CHECK]")
        spacing_chk = shear.get("spacing_check", {})
        spacing_status = spacing_chk.get("status", "PASS")
        max_sp = spacing_chk.get("max_cm", 0.0)
        prov_sp = spacing_chk.get("provided_cm", 0.0)
        print(f"    - Stirrup Spacing Limit  : {spacing_status} (Max = {max_sp:.1f} cm, Provided = {prov_sp:.1f} cm)")
        if spacing_status == "FAIL":
            ex_cm = spacing_chk.get("exceeded_cm", 0.0)
            ex_pct = spacing_chk.get("exceeded_pct", 0.0)
            print(f"      * Exceeded by            = {ex_cm:.1f} cm ({ex_pct:.1f}%)")
            
        min_area_chk = shear.get("min_area_check", {})
        min_area_status = min_area_chk.get("status", "PASS")
        prov_as = min_area_chk.get("provided", 0.0)
        req_as = min_area_chk.get("required", 0.0)
        print(f"    - Minimum Area Limit     : {min_area_status} (Provided Av/s = {prov_as:.4f}, Req Min = {req_as:.4f})")
        
        crushing_chk = shear.get("crushing_check", {})
        crushing_status = crushing_chk.get("status", "PASS")
        crushing_type = crushing_chk.get("type", "Pure Shear")
        print(f"    - Concrete Crushing Limit: {crushing_status} ({crushing_type})")
        
        # Print explicit force metrics if crushing threshold fails
        if crushing_status == "FAIL":
            vs_max_calc = 2.1 * math.sqrt(data.fc_prime) * data.b * d_t
            print(f"      * Provided Vs            = {Vs_actual/1000:.3f} tons")
            print(f"      * Code Maximum Vs,max    = {vs_max_calc/1000:.3f} tons")
        
        print(f"    - Detailing Result       : {shear['detailing_status']}")
        
        print(f"\n    - FINAL SHEAR STATUS   : {shear['status']}")
        print("      [Diagnostic Reason]    :")
        if "\n" in shear['reason']:
            print(f"{shear['reason']}")
        else:
            print(f"        - {shear['reason']}")

        # Step 8: Combined Torsion Verification
        print("\n 8. COMBINED TORSION & SURFACE REINFORCEMENT DESIGN")
        tu_tm = torsion.get('Tu_tm', 0.0)
        tth_tm = torsion.get('T_threshold_tm', 0.0)
        
        print(f"    - Factored Tu   = {tu_tm:.3f} t-m")
        print(f"    - Threshold Tth = {tth_tm:.3f} t-m (Cracking Tcr = {torsion.get('T_cr_tm', 0.0):.3f} t-m)")
        
        print("    [DECISION LOGIC]")
        if tu_tm <= tth_tm:
            print(f"    - Condition: Tu ({tu_tm:.3f} t-m) <= Tth ({tth_tm:.3f} t-m)")
            print("    - Decision : Detailed torsion design not required (Neglect Torsion)")
        else:
            print(f"    - Condition: Tu ({tu_tm:.3f} t-m) > Tth ({tth_tm:.3f} t-m)")
            print("    - Decision : Proceed to torsion design (Torsion Reinforcement Required)")
            
        print(f"    - Core Space    : Aoh = {torsion.get('Aoh_cm2', 0.0):.1f} cm2 | ph = {torsion.get('ph_cm', 0.0):.1f} cm")
        
        # Display explicit longitudinal steel performance data arrays
        al_req = torsion['al_required_mm2']
        al_prov = data.Al_provided_mm2
        al_ur = al_req / al_prov if al_prov > 0 else (999.0 if al_req > 0 else 0.0)
        print(f"    - Req. Longitudinal Steel (Al) = {al_req:.1f} mm2")
        print(f"    - Provided Longitudinal Steel  = {al_prov:.1f} mm2")
        print(f"    - Longitudinal Steel UR        = {al_ur:.3f}")
        
        print(f"    - TORSION STATUS : {torsion['status']} (UR = {torsion['Utilization_Ratio']:.3f})")
        print("      [Diagnostic]:")
        if "\n" in torsion['reason']:
            print(f"{torsion['reason']}")
        else:
            print(f"        - {torsion['reason']}")

        # Print calculated tension development length properties with execution branch traceability
        if Ld is not None and isinstance(Ld, dict):
            print("\n [INFO] REBAR ANCHORAGE")
            print(f"    - Tension Development Length (Ld) = {Ld['Ld_m']:.2f} m (Based on Max Bar Size DB{max_dia:.0f})")
            print(f"    - Detailing Classification        : {Ld['detailing_case']} | {Ld['bar_size_case']}")
        print("-" * 85)

# =========================================================================
# MODULE 7: MODERN HIGH-TECH INTERACTIVE DASHBOARD & DOCUMENT SHEET ENGINE
# =========================================================================
class ReportSheetWindow:
    """Creates an elegant calculation sheet window mimicking a modern clean paper document layout."""
    def __init__(self, parent_root, report_text: str):
        self.top = tk.Toplevel(parent_root)
        self.top.title("ACI 318-14 Structural Design Calculation Sheet")
        self.top.geometry("900x800")
        self.top.configure(bg="#0F172A") # Deep high-tech slate backdrop
        
        # Header Document Action Controls (Emphasizing Minimalist White Space)
        header_frame = tk.Frame(self.top, bg="#0F172A", padx=25, pady=15)
        header_frame.pack(fill=tk.X)
        
        title_lbl = tk.Label(header_frame, text="ACI 318-14 MATHEMATICAL PROOF DOCUMENT", font=("Segoe UI", 11, "bold"), bg="#0F172A", fg="#38BDF8")
        title_lbl.pack(side=tk.LEFT)
        
        close_btn = ttk.Button(header_frame, text="CLOSE DOCUMENT", command=self.top.destroy)
        close_btn.pack(side=tk.RIGHT)

        def copy_entire_report():
            self.top.clipboard_clear()
            self.top.clipboard_append(report_text)
            messagebox.showinfo("Copied", "Entire mathematical proof document copied to clipboard successfully!")

        copy_btn = ttk.Button(header_frame, text="COPY ALL TEXT", command=copy_entire_report)
        copy_btn.pack(side=tk.RIGHT, padx=10)

        # Base vector canvas container for managing rounded paper corner frame cards
        self.canvas_base = tk.Canvas(self.top, bg="#0F172A", borderwidth=0, highlightthickness=0)
        self.canvas_base.pack(fill=tk.BOTH, expand=True, padx=25, pady=(0, 25))
        
        # Clean typography sheet box frame mimicking premium plain paper properties
        self.text_area = tk.Text(
            self.canvas_base, bg="#FFFFFF", fg="#0F172A", 
            font=("Consolas", 10), relief="flat", padx=35, pady=35,
            selectbackground="#93C5FD", selectforeground="#0F172A"
        )
        
        scrollbar = ttk.Scrollbar(self.canvas_base, orient="vertical", command=self.text_area.yview)
        self.text_area.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text_area.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        # Forward execution strings into read-only paper sheets
        self.text_area.insert(tk.END, report_text)
        self.text_area.configure(state=tk.DISABLED)

        # [ADDED FEATURE] Ctrl+A 
        def trigger_select_all(event):
            self.text_area.tag_add("sel", "1.0", "end-1c")
            return "break"
            
        self.text_area.bind("<Control-a>", trigger_select_all)
        self.text_area.bind("<Control-A>", trigger_select_all)

        # [ADDED FEATURE] Ctrl+C
        def trigger_copy(event):
            try:
                selected_text = self.text_area.get("sel.first", "sel.last")
                self.top.clipboard_clear()
                self.top.clipboard_append(selected_text)
            except tk.TclError:
                pass # If no text is selected, no explosion is needed.
            return "break"

        self.text_area.bind("<Control-c>", trigger_copy)
        self.text_area.bind("<Control-C>", trigger_copy)

        self.top.bind("<Configure>", lambda e: self.redraw_paper_sheet_bounds())

    def draw_rounded_rectangle(self, canvas, x1, y1, x2, y2, radius, fill_color):
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius, x2, y2 - radius, x2, y2,
            x2 - radius, y2, x1 + radius, y2, x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1
        ]
        return canvas.create_polygon(points, fill=fill_color, smooth=True, splinesteps=30)

    def redraw_paper_sheet_bounds(self):
        """Re-renders modern curved drop shadows behind paper document frames dynamically."""
        self.canvas_base.delete("paper_shadow_bg")
        w = self.canvas_base.winfo_width()
        h = self.canvas_base.winfo_height()
        if w > 50 and h > 50:
            self.draw_rounded_rectangle(self.canvas_base, 0, 0, w - 20, h, 20, "#FFFFFF")
            self.canvas_base.tag_lower("paper_shadow_bg")


class BeamCalDashboard:
    def __init__(self, root_window):
        self.root = root_window
        self.root.title("BeamCal ENGINE v1.0 - Cyber Structural Panel")
        self.root.geometry("1300x820")
        self.root.minsize(1200, 750)
        
        # 1. Path Management Layer
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.json_filename = os.path.join(base_dir, "input.json")
        
        # 2. Synchronize In-Memory State Pipeline Registers
        self.sync_initial_data_state()
        
        # 3. Modern Dark Tech Obsidian Theme Setup
        self.bg_main = "#0F172A" # Premium Slate Blue Midnight Dark
        self.bg_card = "#1E293B" # Card frame backgrounds
        self.fg_light = "#F8FAFC" # Bright slate white for readability
        self.accent_blue = "#38BDF8" # Cyber Sky Blue Accent Highlights
        self.root.configure(bg=self.bg_main)
        
        self.configure_dark_tech_styles()
        
        # 4. Interface Split Screen Structural Containers
        self.left_panel = tk.Frame(self.root, width=550, bg="#1E293B", bd=0)
        self.left_panel.pack(side=tk.LEFT, fill=tk.Y)
        self.left_panel.pack_propagate(False)
        
        self.right_panel = tk.Frame(self.root, bg=self.bg_main, bd=0)
        self.right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        # 5. Core Operational Symmetrical Tabs Navigator (Ensuring Layout Balance)
        self.notebook = ttk.Notebook(self.left_panel)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)
        
        self.tab_geo = tk.Frame(self.notebook, bg="#1E293B", padx=15, pady=15)
        self.tab_forces = tk.Frame(self.notebook, bg="#1E293B", padx=15, pady=15)
        self.tab_rebars = tk.Frame(self.notebook, bg="#1E293B", padx=15, pady=15)
        
        self.notebook.add(self.tab_geo, text=" Geometry & Material ")
        self.notebook.add(self.tab_forces, text=" Design Forces ")
        self.notebook.add(self.tab_rebars, text=" Dynamic Rebars ")
        
        self.rebar_rows = {"INITIAL": {"top": [], "bot": []}, "MID": {"top": [], "bot": []}, "END": {"top": [], "bot": []}}
        
        # 6. Hydrate and Build Control Fields
        self.build_geometry_tab()
        self.build_forces_tab()
        self.build_rebars_tab()
        self.build_high_tech_preview_panel()
        
        # Global Action Core Command Panel Footer
        btn_frame = tk.Frame(self.left_panel, bg="#1E293B", padx=15, pady=15)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)
        
        sync_frame = tk.Frame(btn_frame, bg="#1E293B")
        sync_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(sync_frame, text="LOAD FROM JSON", style="Save.TButton", command=self.load_state_from_json_file).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(sync_frame, text="SAVE TO JSON", style="Save.TButton", command=self.commit_state_to_json_file).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))
        
        ttk.Button(btn_frame, text="GENERATE CALCULATION PROOF SHEET", style="Action.TButton", command=self.trigger_backend_calculation).pack(fill=tk.X, ipady=8)

        # Clear drawing cache parameters to fix startup blank lags
        self.root.update_idletasks()
        self.update_live_preview()

    def configure_dark_tech_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=self.bg_main, borderwidth=0)
        style.configure("TNotebook.Tab", background="#334155", foreground="#94A3B8", font=("Segoe UI", 9, "bold"), borderwidth=0, padding=[14, 8])
        style.map("TNotebook.Tab", background=[("selected", self.accent_blue)], foreground=[("selected", "#0F172A")])
        style.configure("TLabel", background="#1E293B", foreground=self.fg_light, font=("Segoe UI", 9))
        style.configure("Header.TLabel", background="#1E293B", foreground=self.accent_blue, font=("Segoe UI", 11, "bold"))
        style.configure("TEntry", fieldbackground="#334155", foreground="#FFFFFF", borderwidth=0, padding=4)
        style.configure("Action.TButton", font=("Segoe UI", 10, "bold"), background=self.accent_blue, foreground="#0F172A")
        style.map("Action.TButton", background=[("active", "#0EA5E9")])
        style.configure("Save.TButton", font=("Segoe UI", 9, "bold"), background="#475569", foreground="#FFFFFF")
        style.map("Save.TButton", background=[("active", "#334155")])

    def sync_initial_data_state(self):
        if os.path.exists(self.json_filename):
            try:
                with open(self.json_filename, "r", encoding="utf-8") as f:
                    self.raw_state = json.load(f)
                    print(f"[INFO] Persistent profile loaded cleanly from '{self.json_filename}'")
                    return
            except Exception:
                print("[WARNING] Local file corrupted. Loading secure hardcoded template profile arrays into memory.")
        
        self.raw_state = {
            "fc_prime": 240.0, "fy": 4000.0, "fy_shear": 4000.0,
            "b": 60.0, "h": 80.0, "covering": 4.0,
            "stirrup_dia": 12.0, "stirrup_spacing": 10.0, "stirrup_legs": 4,
            "Al_provided_mm2": 1397.0,
            "forces": {
                "INITIAL": {"Mu": 45582.5, "Vu": 98758.5, "Pu": 0.0, "Tu": 8753.31},
                "MID":     {"Mu": -75917.952, "Vu": 54336.338, "Pu": 0.0, "Tu": 8753.31},
                "END":     {"Mu": 0.0, "Vu": 0.0, "Pu": 0.0, "Tu": 0.0}
            },
            "rebars": {
                "INITIAL": {
                    "top": [{"dia": 25.0, "qty": 4, "clear_dist": 0.0}],
                    "bot": [{"dia": 25.0, "qty": 4, "clear_dist": 0.0}, {"dia": 25.0, "qty": 2, "clear_dist": 2.5}]
                },
                "MID": {
                    "top": [{"dia": 25.0, "qty": 4, "clear_dist": 0.0}, {"dia": 25.0, "qty": 3, "clear_dist": 2.5}],
                    "bot": [{"dia": 25.0, "qty": 4, "clear_dist": 0.0}]
                },
                "END": {"top": [], "bot": []}
            }
        }

    def build_geometry_tab(self):
        ttk.Label(self.tab_geo, text="DIMENSIONS & MATERIAL CAPACITY", style="Header.TLabel").pack(anchor=tk.W, pady=(0, 8))
        
        self.b_var = tk.DoubleVar(value=self.raw_state.get("b", 60.0))
        self.h_var = tk.DoubleVar(value=self.raw_state.get("h", 80.0))
        self.cov_var = tk.DoubleVar(value=self.raw_state.get("covering", 4.0))
        
        for var in [self.b_var, self.h_var, self.cov_var]:
            var.trace_add("write", lambda *args: self.update_live_preview())
            
        ttk.Label(self.tab_geo, text="Beam Width, b (cm):").pack(anchor=tk.W, pady=(4, 0))
        ttk.Entry(self.tab_geo, textvariable=self.b_var).pack(fill=tk.X, pady=3)
        
        ttk.Label(self.tab_geo, text="Beam Height, h (cm):").pack(anchor=tk.W, pady=(4, 0))
        ttk.Entry(self.tab_geo, textvariable=self.h_var).pack(fill=tk.X, pady=3)
        
        ttk.Label(self.tab_geo, text="Concrete Covering, cov (cm):").pack(anchor=tk.W, pady=(4, 0))
        ttk.Entry(self.tab_geo, textvariable=self.cov_var).pack(fill=tk.X, pady=3)
        
        self.fc_var = tk.DoubleVar(value=self.raw_state.get("fc_prime", 240.0))
        self.fy_var = tk.DoubleVar(value=self.raw_state.get("fy", 4000.0))
        self.fyt_var = tk.DoubleVar(value=self.raw_state.get("fy_shear", 4000.0))
        
        ttk.Label(self.tab_geo, text="Concrete Cylinder Strength, fc' (ksc):").pack(anchor=tk.W, pady=(10, 0))
        ttk.Entry(self.tab_geo, textvariable=self.fc_var).pack(fill=tk.X, pady=3)
        
        ttk.Label(self.tab_geo, text="Longitudinal Bars Yield, fy (ksc):").pack(anchor=tk.W, pady=(4, 0))
        ttk.Entry(self.tab_geo, textvariable=self.fy_var).pack(fill=tk.X, pady=3)
        
        ttk.Label(self.tab_geo, text="Stirrups Shear Yield, fyt (ksc):").pack(anchor=tk.W, pady=(4, 0))
        ttk.Entry(self.tab_geo, textvariable=self.fyt_var).pack(fill=tk.X, pady=3)
        
        self.st_dia_var = tk.DoubleVar(value=self.raw_state.get("stirrup_dia", 12.0))
        self.st_space_var = tk.DoubleVar(value=self.raw_state.get("stirrup_spacing", 10.0))
        self.st_legs_var = tk.IntVar(value=self.raw_state.get("stirrup_legs", 4))
        self.al_prov_var = tk.DoubleVar(value=self.raw_state.get("Al_provided_mm2", 1397.0))
        
        for var in [self.st_dia_var, self.st_space_var, self.st_legs_var]:
             var.trace_add("write", lambda *args: self.update_live_preview())

        ttk.Label(self.tab_geo, text="Stirrup Diameter Size (mm):").pack(anchor=tk.W, pady=(10, 0))
        ttk.Entry(self.tab_geo, textvariable=self.st_dia_var).pack(fill=tk.X, pady=3)
        
        box_split = tk.Frame(self.tab_geo, bg="#1E293B")
        box_split.pack(fill=tk.X, pady=2)
        
        f_left = tk.Frame(box_split, bg="#1E293B")
        f_left.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Label(f_left, text="Stirrup Spacing, s (cm):").pack(anchor=tk.W)
        ttk.Entry(f_left, textvariable=self.st_space_var).pack(fill=tk.X, pady=1)
        
        f_right = tk.Frame(box_split, bg="#1E293B")
        f_right.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))
        ttk.Label(f_right, text="Stirrup Legs (qty):").pack(anchor=tk.W)
        ttk.Entry(f_right, textvariable=self.st_legs_var).pack(fill=tk.X, pady=1)
        
        ttk.Label(self.tab_geo, text="Provided Longitudinal Torsion Steel, Al (mm2):").pack(anchor=tk.W, pady=(10, 0))
        ttk.Entry(self.tab_geo, textvariable=self.al_prov_var).pack(fill=tk.X, pady=3)

    def build_forces_tab(self):
        sections = ["INITIAL", "MID", "END"]
        self.force_vars = {}
        for sec in sections:
            ttk.Label(self.tab_forces, text=f"{sec} SPAN LOADING METRICS", style="Header.TLabel").pack(anchor=tk.W, pady=(12, 6))
            frame = tk.Frame(self.tab_forces, bg="#1E293B")
            frame.pack(fill=tk.X, pady=2)
            
            self.force_vars[sec] = {
                "Mu": tk.DoubleVar(value=self.raw_state["forces"][sec].get("Mu", 0.0)),
                "Vu": tk.DoubleVar(value=self.raw_state["forces"][sec].get("Vu", 0.0)),
                "Pu": tk.DoubleVar(value=self.raw_state["forces"][sec].get("Pu", 0.0)),
                "Tu": tk.DoubleVar(value=self.raw_state["forces"][sec].get("Tu", 0.0))
            }
            
            keys = ["Mu", "Vu", "Pu", "Tu"]
            labels = ["Mu (kg-m)", "Vu (kg)", "Pu (kg)", "Tu (kg-m)"]
            for i, (lbl, key) in enumerate(zip(labels, keys)):
                ttk.Label(frame, text=lbl, font=("Segoe UI", 8, "bold"), foreground="#94A3B8").grid(row=0, column=i, padx=5, sticky=tk.W)
                ttk.Entry(frame, textvariable=self.force_vars[sec][key], width=13).grid(row=1, column=i, padx=5, pady=4)

    def build_rebars_tab(self):
        """Constructs scrollable frameworks managing rebar listings concurrently without dropping matrices."""
        top_bar = tk.Frame(self.tab_rebars, bg="#1E293B")
        top_bar.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(top_bar, text="SELECT MONITOR SECTION:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=2)
        self.active_sec_var = tk.StringVar(value="INITIAL")
        sec_menu = ttk.Combobox(top_bar, textvariable=self.active_sec_var, values=["INITIAL", "MID", "END"], state="readonly", width=14)
        sec_menu.pack(side=tk.LEFT, padx=5)
        sec_menu.bind("<<ComboboxSelected>>", lambda e: self.update_live_preview())

        layout_container = tk.Frame(self.tab_rebars, bg="#1E293B")
        layout_container.pack(fill=tk.BOTH, expand=True)

        scroll_win = tk.Canvas(layout_container, bg="#1E293B", highlightthickness=0)
        scrollbar = ttk.Scrollbar(layout_container, orient="vertical", command=scroll_win.yview)
        scroll_win.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_win.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.all_sections_container = tk.Frame(scroll_win, bg="#1E293B")
        canvas_window = scroll_win.create_window((0, 0), window=self.all_sections_container, anchor=tk.NW)
        
        scroll_win.bind("<Configure>", lambda e: scroll_win.itemconfig(canvas_window, width=e.width))
        self.all_sections_container.bind("<Configure>", lambda e: scroll_win.configure(scrollregion=scroll_win.bbox("all")))

        def _on_mousewheel(event):
            try:
                if self.notebook.index(self.notebook.select()) == 2:
                    scroll_win.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass
        self.root.bind_all("<MouseWheel>", _on_mousewheel)

        self.rebuild_all_rebar_sections_ui()

    def rebuild_all_rebar_sections_ui(self):
        """Builds separate row generation cards matching INITIAL, MID, END spans into RAM."""
        for widget in self.all_sections_container.winfo_children():
            widget.destroy()

        for sec in ["INITIAL", "MID", "END"]:
            self.rebar_rows[sec]["top"] = []
            self.rebar_rows[sec]["bot"] = []

            sec_card = tk.LabelFrame(self.all_sections_container, text=f" {sec} SECTION SPAN LAYOUT ", font=("Segoe UI", 10, "bold"), bg="#1E293B", fg="#38BDF8", bd=1, relief="flat", padx=8, pady=8)
            sec_card.pack(fill=tk.X, padx=2, pady=10)
            
            # Render Top Face Zone
            ttk.Label(sec_card, text="Tension Rebars (Top Face Layers):", font=("Segoe UI", 8, "bold"), foreground="#94A3B8").pack(anchor=tk.W, padx=5, pady=(2, 0))
            top_box = tk.Frame(sec_card, bg="#1E293B")
            top_box.pack(fill=tk.X, padx=5)
            for idx, layer in enumerate(self.raw_state["rebars"][sec].get("top", [])):
                self.render_rebar_row_inputs(top_box, sec, "top", idx, layer)
                
            ctrl_top = tk.Frame(sec_card, bg="#1E293B")
            ctrl_top.pack(fill=tk.X, padx=5, pady=4)
            ttk.Button(ctrl_top, text="+ Add Layer", style="Save.TButton", command=lambda s=sec: self.add_new_rebar_layer(s, "top"), width=12).pack(side=tk.LEFT, padx=2)
            ttk.Button(ctrl_top, text="- Remove", style="Save.TButton", command=lambda s=sec: self.remove_last_rebar_layer(s, "top"), width=12).pack(side=tk.LEFT, padx=2)
            
            # Render Bottom Face Zone
            ttk.Label(sec_card, text="Compression Rebars (Bottom Face Layers):", font=("Segoe UI", 8, "bold"), foreground="#94A3B8").pack(anchor=tk.W, padx=5, pady=(6, 0))
            bot_box = tk.Frame(sec_card, bg="#1E293B")
            bot_box.pack(fill=tk.X, padx=5)
            for idx, layer in enumerate(self.raw_state["rebars"][sec].get("bot", [])):
                self.render_rebar_row_inputs(bot_box, sec, "bot", idx, layer)
                
            ctrl_bot = tk.Frame(sec_card, bg="#1E293B")
            ctrl_bot.pack(fill=tk.X, padx=5, pady=4)
            ttk.Button(ctrl_bot, text="+ Add Layer", style="Save.TButton", command=lambda s=sec: self.add_new_rebar_layer(s, "bot"), width=12).pack(side=tk.LEFT, padx=2)
            ttk.Button(ctrl_bot, text="- Remove", style="Save.TButton", command=lambda s=sec: self.remove_last_rebar_layer(s, "bot"), width=12).pack(side=tk.LEFT, padx=2)

    def render_rebar_row_inputs(self, master_frame, sec, face, idx, data):
        row = tk.Frame(master_frame, bg="#1E293B")
        row.pack(fill=tk.X, pady=2)
        
        ttk.Label(row, text=f"L{idx+1} Qty:", width=8, foreground="#E2E8F0").grid(row=0, column=0, sticky=tk.W)
        q_var = tk.IntVar(value=data.get("qty", 0))
        d_var = tk.DoubleVar(value=data.get("dia", 0.0))
        c_var = tk.DoubleVar(value=data.get("clear_dist", 0.0))
        
        for var in [q_var, d_var, c_var]:
            var.trace_add("write", lambda *args: self.update_live_preview())
            
        ttk.Entry(row, textvariable=q_var, width=5).grid(row=0, column=1, padx=3)
        ttk.Label(row, text="D(mm):", foreground="#94A3B8").grid(row=0, column=2, padx=2)
        ttk.Entry(row, textvariable=d_var, width=7).grid(row=0, column=3, padx=3)
        ttk.Label(row, text="Clear(cm):", foreground="#94A3B8").grid(row=0, column=4, padx=2)
        ttk.Entry(row, textvariable=c_var, width=6).grid(row=0, column=5, padx=3)
        
        self.rebar_rows[sec][face].append({"qty": q_var, "dia": d_var, "clear_dist": c_var})

    def add_new_rebar_layer(self, sec, face):
        self.sync_all_active_ui_values_to_raw_state()
        default_gap = 2.5 if len(self.raw_state["rebars"][sec][face]) > 0 else 0.0
        self.raw_state["rebars"][sec][face].append({"dia": 25.0, "qty": 2, "clear_dist": default_gap})
        
        self.rebuild_all_rebar_sections_ui()
        self.update_live_preview()

    def remove_last_rebar_layer(self, sec, face):
        if self.raw_state["rebars"][sec][face]:
            self.sync_all_active_ui_values_to_raw_state()
            self.raw_state["rebars"][sec][face].pop() #  CLEANED UP DUPLICATE BUG IN CODE SNAPSHOT
            self.rebuild_all_rebar_sections_ui()
            self.update_live_preview()

    def build_high_tech_preview_panel(self):
        """Constructs the vector drawing canvas window frame with correct padding allocations."""
        # FIX: Remove the tuple pady=(15, 0) from the Frame constructor, keep it clean
        title_frame = tk.Frame(self.right_panel, bg="#0F172A")
        
        # Move the directional padding tuple directly into the layout geometry manager (.pack)
        title_frame.pack(fill=tk.X, padx=15, pady=(15, 0))
        
        ttk.Label(title_frame, text="DYNAMIC CROSS-SECTION MONITOR VIEW", style="Header.TLabel", background="#0F172A").pack(side=tk.LEFT)
        
        self.right_canvas = tk.Canvas(self.right_panel, bg=self.bg_main, borderwidth=0, highlightthickness=0)
        self.right_canvas.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        self.right_canvas.bind("<Configure>", lambda e: self.update_live_preview())

    def draw_rounded_card_background(self, x1, y1, x2, y2, radius, fill_color):
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius, x2, y2 - radius, x2, y2,
            x2 - radius, y2, x1 + radius, y2, x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1
        ]
        return self.right_canvas.create_polygon(points, fill=fill_color, smooth=True, splinesteps=30)

    def update_live_preview(self):
        self.right_canvas.delete("all")
        try:
            w = self.right_canvas.winfo_width()
            h_canvas = self.right_canvas.winfo_height()
            if w < 100 or h_canvas < 100: 
                return
            
            # Modern Smooth Rounded card visual wrapper base
            self.draw_rounded_card_background(0, 0, w, h_canvas, 24, self.bg_card)
            
            b = self.b_var.get()
            h = self.h_var.get()
            cov = self.cov_var.get()
            st_dia = self.st_dia_var.get() / 10.0
            
            if b <= 0 or h <= 0 or cov < 0 or (2 * cov) >= b or (2 * cov) >= h:
                self.right_canvas.create_text(w/2, h_canvas/2, text="[CRITICAL BOUNDS OVERFLOW]", fill="#EF4444", font=("Segoe UI", 12, "bold"))
                return
                
            box_max_w, box_max_h = w - 180, h_canvas - 160
            scale = min(box_max_w / b, box_max_h / h)
            
            x_start = (w - (b * scale)) / 2
            y_start = (h_canvas - (h * scale)) / 2
            x_end, y_end = x_start + (b * scale), y_start + (h * scale)
            cov_px = cov * scale
            
            # 1. Outer Main Concrete Profile Bound
            self.right_canvas.create_rectangle(x_start, y_start, x_end, y_end, outline="#475569", fill="#0F172A", width=3)
            
            #2. Retrieve the missing stirrup loop box.
            self.right_canvas.create_rectangle(x_start + cov_px, y_start + cov_px, x_end - cov_px, y_end - cov_px, outline=self.accent_blue, width=2)
            
            # 3.Move the steel casing text to the bottom outer edge + drag the arrow pointing upwards to the casing line to prevent the steel from overlapping.
            st_text = f"Stirrup: DB{self.st_dia_var.get():.0f} @ {self.st_space_var.get():.1f} cm (Legs: {self.st_legs_var.get()})"
            text_y = y_end + 35
            self.right_canvas.create_text((x_start + x_end)/2, text_y, text=st_text, fill=self.accent_blue, font=("Segoe UI", 9, "bold"))
            # Draw a leader line with an arrow  pointing upwards towards the lower horizontal steel reinforcement bar.
            self.right_canvas.create_line((x_start + x_end)/2, text_y - 12, (x_start + x_end)/2, y_end - cov_px, fill=self.accent_blue, width=1.5, arrow=tk.LAST)
            
            sec = self.active_sec_var.get()
            self.sync_active_ui_values_to_raw_state(sec)
            
            # 4. Draw Dynamic Top Layers (Ovals + Callouts With 0-Guard Check)
            curr_y_top = y_start + cov_px + (st_dia * scale)
            for layer in self.raw_state["rebars"][sec].get("top", []):
                qty = layer.get("qty", 0)
                dia = layer.get("dia", 0.0)
                clear_dist = layer.get("clear_dist", 0.0)
                
                # Checking criteria: If no reinforcement is used or the value is 0 -> Skip this layer; do not show it.
                if qty <= 0 or dia <= 0:
                    continue
                    
                d_px = (dia / 10.0) * scale
                curr_y_top += (clear_dist * scale) + (d_px / 2.0)
                
                # Drag a line to indicate details to the right edge.
                self.right_canvas.create_line(x_end - cov_px, curr_y_top, x_end + 30, curr_y_top, fill="#EF4444", width=1)
                rebar_text = f"{qty}-DB{dia:.0f}"
                if clear_dist > 0:
                    rebar_text += f" (Clear={clear_dist:.1f}cm)"
                self.right_canvas.create_text(x_end + 35, curr_y_top, text=rebar_text, fill="#EF4444", font=("Segoe UI", 9, "bold"), anchor=tk.W)
                
                # Draw circular steel wire dots based on actual pixels.
                x_avail = (x_end - cov_px - st_dia*scale - d_px/2.0) - (x_start + cov_px + st_dia*scale + d_px/2.0)
                for i in range(qty):
                    cx = (x_start + cov_px + st_dia*scale + d_px/2.0) + (x_avail * i / (qty - 1) if qty > 1 else x_avail / 2)
                    self.right_canvas.create_oval(cx - d_px/2.0, curr_y_top - d_px/2.0, cx + d_px/2.0, curr_y_top + d_px/2.0, fill="#EF4444", outline="#FFFFFF", width=1)
                    
                curr_y_top += (d_px / 2.0)

            # 5. Draw Dynamic Bottom Layers (Ovals + Callouts With 0-Guard Check)
            curr_y_bot = y_end - cov_px - (st_dia * scale)
            for layer in self.raw_state["rebars"][sec].get("bot", []):
                qty = layer.get("qty", 0)
                dia = layer.get("dia", 0.0)
                clear_dist = layer.get("clear_dist", 0.0)
                
                # Checking criteria: If no reinforcement is used or the value is 0 -> Skip this layer; do not show it.
                if qty <= 0 or dia <= 0:
                    continue
                    
                d_px = (dia / 10.0) * scale
                curr_y_bot -= (clear_dist * scale) + (d_px / 2.0)
                
                # Drag a line to indicate details to the right edge.
                self.right_canvas.create_line(x_end - cov_px, curr_y_bot, x_end + 30, curr_y_bot, fill="#10B981", width=1)
                rebar_text = f"{qty}-DB{dia:.0f}"
                if clear_dist > 0:
                    rebar_text += f" (Clear={clear_dist:.1f}cm)"
                self.right_canvas.create_text(x_end + 35, curr_y_bot, text=rebar_text, fill="#10B981", font=("Segoe UI", 9, "bold"), anchor=tk.W)
                
                # Draw circular steel wire dots based on actual pixels.
                x_avail = (x_end - cov_px - st_dia*scale - d_px/2.0) - (x_start + cov_px + st_dia*scale + d_px/2.0)
                for i in range(qty):
                    cx = (x_start + cov_px + st_dia*scale + d_px/2.0) + (x_avail * i / (qty - 1) if qty > 1 else x_avail / 2)
                    self.right_canvas.create_oval(cx - d_px/2.0, curr_y_bot - d_px/2.0, cx + d_px/2.0, curr_y_bot + d_px/2.0, fill="#10B981", outline="#FFFFFF", width=1)
                    
                curr_y_bot -= (d_px / 2.0)
            
            # 6. Dynamic Dimensions Metrics Labels
            self.right_canvas.create_line(x_start, y_start - 18, x_end, y_start - 18, fill=self.fg_light, width=1, arrow=tk.BOTH)
            self.right_canvas.create_text((x_start + x_end)/2, y_start - 32, text=f"b = {b:.1f} cm", fill=self.fg_light, font=("Segoe UI", 9, "bold"))
            
            self.right_canvas.create_line(x_start - 18, y_start, x_start - 18, y_end, fill=self.fg_light, width=1, arrow=tk.BOTH)
            self.right_canvas.create_text(x_start - 55, (y_start + y_end)/2, text=f"h = {h:.1f} cm", fill=self.fg_light, font=("Segoe UI", 9, "bold"))
            
            self.right_canvas.create_line(x_end + 18, y_start, x_end + 18, y_start + cov_px, fill=self.accent_blue, width=1, arrow=tk.BOTH)
            self.right_canvas.create_line(x_end, y_start + cov_px, x_end + 25, y_start + cov_px, fill="#475569", width=1) 
            self.right_canvas.create_text(x_end + 65, y_start + cov_px/2, text=f"cov = {cov:.1f} cm", fill=self.accent_blue, font=("Segoe UI", 8, "bold"))
            
            self.right_canvas.create_text(25, h_canvas - 25, text=f"MONITOR SEC SPAN: [{sec}] | IN-MEMORY ARCHITECTURE PANEL VIEW", fill="#64748B", font=("Segoe UI", 8, "italic"), anchor=tk.W)
            
        except (ValueError, tk.TclError):
            pass

    def sync_active_ui_values_to_raw_state(self, sec):
        for face in ["top", "bot"]:
            if sec in self.rebar_rows and face in self.rebar_rows[sec] and self.rebar_rows[sec][face]:
                self.raw_state["rebars"][sec][face] = []
                for row_data in self.rebar_rows[sec][face]:
                    try:
                        q, d, c = row_data["qty"].get(), row_data["dia"].get(), row_data["clear_dist"].get()
                        if q > 0:
                            self.raw_state["rebars"][sec][face].append({"qty": q, "dia": d, "clear_dist": c})
                    except Exception:
                        pass

    def sync_all_active_ui_values_to_raw_state(self):
        for sec in ["INITIAL", "MID", "END"]:
            self.sync_active_ui_values_to_raw_state(sec)

    def update_raw_state_from_all_ui_fields(self):
        self.raw_state["b"] = self.b_var.get()
        self.raw_state["h"] = self.h_var.get()
        self.raw_state["covering"] = self.cov_var.get()
        self.raw_state["fc_prime"] = self.fc_var.get()
        self.raw_state["fy"] = self.fy_var.get()
        self.raw_state["fy_shear"] = self.fyt_var.get()
        self.raw_state["stirrup_dia"] = self.st_dia_var.get()
        self.raw_state["stirrup_spacing"] = self.st_space_var.get()
        self.raw_state["stirrup_legs"] = self.st_legs_var.get()
        self.raw_state["Al_provided_mm2"] = self.al_prov_var.get()
        
        for sec in ["INITIAL", "MID", "END"]:
            for key in ["Mu", "Vu", "Pu", "Tu"]:
                self.raw_state["forces"][sec][key] = self.force_vars[sec][key].get()
        self.sync_all_active_ui_values_to_raw_state()

    def compile_current_input_data_object(self) -> BeamInputData:
        self.update_raw_state_from_all_ui_fields()
        return BeamInputData(
            fc_prime=self.raw_state["fc_prime"],
            fy=self.raw_state["fy"],
            fy_shear=self.raw_state["fy_shear"],
            b=Unit.to_cm(self.raw_state["b"], "cm"),
            h=Unit.to_cm(self.raw_state["h"], "cm"),
            covering=Unit.to_cm(self.raw_state["covering"], "cm"),
            stirrup_dia=self.raw_state["stirrup_dia"],
            stirrup_spacing=self.raw_state["stirrup_spacing"],
            stirrup_legs=self.raw_state["stirrup_legs"],
            Al_provided_mm2=self.raw_state["Al_provided_mm2"],
            
            forcesinitial=SectionForces(**self.raw_state["forces"]["INITIAL"]),
            forcesmid=SectionForces(**self.raw_state["forces"]["MID"]),
            forcesend=SectionForces(**self.raw_state["forces"]["END"]),
            
            topinitial_rebars=[RebarLayer(**r) for r in self.raw_state["rebars"]["INITIAL"]["top"]] or [RebarLayer(dia=0.0, qty=0)],
            botinitial_rebars=[RebarLayer(**r) for r in self.raw_state["rebars"]["INITIAL"]["bot"]] or [RebarLayer(dia=0.0, qty=0)],
            topmid_rebars=[RebarLayer(**r) for r in self.raw_state["rebars"]["MID"]["top"]] or [RebarLayer(dia=0.0, qty=0)],
            botmid_rebars=[RebarLayer(**r) for r in self.raw_state["rebars"]["MID"]["bot"]] or [RebarLayer(dia=0.0, qty=0)],
            topend_rebars=[RebarLayer(**r) for r in self.raw_state["rebars"]["END"]["top"]] or [RebarLayer(dia=0.0, qty=0)],
            botend_rebars=[RebarLayer(**r) for r in self.raw_state["rebars"]["END"]["bot"]] or [RebarLayer(dia=0.0, qty=0)]
        )

    def load_state_from_json_file(self):
        if not os.path.exists(self.json_filename):
            messagebox.showerror("Sync Error", "Target structural profile file 'input.json' missing in directory working path.")
            return
        try:
            with open(self.json_filename, "r", encoding="utf-8") as f:
                self.raw_state = json.load(f)
            
            self.b_var.set(self.raw_state.get("b", 60.0))
            self.h_var.set(self.raw_state.get("h", 80.0))
            self.cov_var.set(self.raw_state.get("covering", 4.0))
            self.fc_var.set(self.raw_state.get("fc_prime", 240.0))
            self.fy_var.set(self.raw_state.get("fy", 4000.0))
            self.fyt_var.set(self.raw_state.get("fy_shear", 4000.0))
            self.st_dia_var.set(self.raw_state.get("stirrup_dia", 12.0))
            self.st_space_var.set(self.raw_state.get("stirrup_spacing", 10.0))
            self.st_legs_var.set(self.raw_state.get("stirrup_legs", 4))
            self.al_prov_var.set(self.raw_state.get("Al_provided_mm2", 1397.0))
            
            for sec in ["INITIAL", "MID", "END"]:
                for key in ["Mu", "Vu", "Pu", "Tu"]:
                    self.force_vars[sec][key].set(self.raw_state["forces"][sec][key])
            
            self.rebuild_all_rebar_sections_ui()
            self.update_live_preview()
            messagebox.showinfo("Sync Successful", "RAM configurations synchronized smoothly from input.json profiles.")
        except Exception as e:
            messagebox.showerror("Sync Failure", f"Failed to execute dynamic data parsing mapping: {str(e)}")

    def commit_state_to_json_file(self):
        try:
            self.update_raw_state_from_all_ui_fields()
            with open(self.json_filename, "w", encoding="utf-8") as f:
                json.dump(self.raw_state, f, indent=4)
            messagebox.showinfo("Export Successful", "Current structural matrix state saved into input.json profile settings.")
        except Exception as e:
            messagebox.showerror("Export Failure", f"Failed to capture parameters: {str(e)}")

    def trigger_backend_calculation(self):
        try:
            structured_data = self.compile_current_input_data_object()
            output_buffer = io.StringIO()
            with redirect_stdout(output_buffer):
                execute_engine(structured_data)
            captured_report = output_buffer.getvalue()
            
            ReportSheetWindow(self.root, captured_report)
        except Exception as e:
            messagebox.showerror("Execution Aborted", f"Solver engine collapsed inside analytical math loops: {str(e)}")


if __name__ == "__main__":
    root = tk.Tk()
    app = BeamCalDashboard(root)
    root.mainloop()